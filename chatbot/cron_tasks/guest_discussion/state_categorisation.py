from chatbot.models import CompanyBot, Voice, VoiceProvider, VoiceType, LanguageMapping
from chatbot.models import Story
from chatbot.translate.ai4Bharat.text_lang_detect import call_ai4bharat_text_lang_detect_api
from chatbot.translate.google.google_translate import translate_text
from datetime import date
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from typing import Any, Dict, List, Optional
import json
import logging
import re

logger = logging.getLogger('django')


# -------------- LANGUAGE HELPERS ------------------

def _is_likely_english(text: str) -> bool:
    if not text:
        return False
    ascii_count = sum(1 for c in text if ord(c) < 128)
    return (ascii_count / len(text)) > 0.8


def _translate_location_to_english(location_text: str) -> Optional[str]:

    SUPPORTED_LANGUAGES = {'hi', 'kn', 'te'}

    try:
        detect_response = call_ai4bharat_text_lang_detect_api(location_text)
        if not detect_response or detect_response.get('status') != 200:
            logger.warning(f"Language detection failed for location '{location_text}': {detect_response}")
            return None

        detected_lang = detect_response.get('content')
        logger.info(f"Detected language '{detected_lang}' for location '{location_text}'")

        if detected_lang not in SUPPORTED_LANGUAGES:
            logger.warning(f"Detected language '{detected_lang}' not in supported set {SUPPORTED_LANGUAGES}; skipping translation")
            return None

        chaupal_bot = CompanyBot.objects.filter(route='/shikshalokam_chaupal').first()
        if not chaupal_bot:
            logger.warning("CompanyBot '/shikshalokam_chaupal' not found; skipping translation")
            return None

        voice = Voice.objects.filter(
            company_bot=chaupal_bot,
            provider=VoiceProvider.GOOGLE,
            type=VoiceType.TextToText,
            language=detected_lang,
        ).first()
        if not voice:
            logger.warning(f"No Google TextToText Voice for language='{detected_lang}' on '/shikshalokam_chaupal'; skipping translation")
            return None

        secret = settings.SECRETS
        source_lang = LanguageMapping.get_google_translate_language(detected_lang)
        logger.info(f"Translating location '{location_text}' from '{detected_lang}' to English")

        response = translate_text(
            text=location_text,
            project_id=secret.get('project_id'),
            source_language_code=source_lang,
            target_language_code='en',
            voice_provider=voice,
        )
        if response and response.get('status') == 200:
            translated = response['content']
            logger.info(f"Location translated: '{location_text}' → '{translated}'")
            return translated
        else:
            logger.error(f"Translation API failed for '{location_text}': {response}")
            return None
    except Exception as e:
        logger.error(f"Error translating location '{location_text}': {e}")
        return None


# -------------- DATA LOADING ------------------

def load_mapping_from_bot(company_bot) -> Dict[str, Any]:
    """Load mapping JSON from CompanyBot.dynamic_context."""
    try:
        dynamic_context = company_bot.dynamic_context
        if not dynamic_context:
            logger.error(f"CompanyBot {company_bot.id} has no dynamic_context")
            return {}
        if isinstance(dynamic_context, dict):
            return dynamic_context
        return json.loads(dynamic_context)
    except (json.JSONDecodeError, AttributeError) as e:
        logger.error(f"Error loading mapping from CompanyBot {company_bot.id}: {e}")
        return {}


# -------------- PATTERN MATCHING ------------------

def _normalize_text(text: str) -> str:
    text = re.sub(r'[,.]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _matches(pattern: str, text: str) -> bool:
    """Match a pattern against text, ensuring it is not part of a larger word."""
    escaped = re.escape(pattern)
    return bool(re.search(r'(?<![a-zA-Z])' + escaped + r'(?![a-zA-Z])', text, re.IGNORECASE))


def _match_patterns(patterns: List[str], text: str) -> bool:
    return any(_matches(p, text) for p in patterns)


# -------------- CORE CATEGORISATION ------------------

def _match_location(location_text: str, mapping_data: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """
    Layers 1 & 2: match district then state against location text only.

    Resolution order:
      1. If state pattern found in text: restrict district search to that state only.
         District match -> return {state, district}. No district match -> return state-only.
      2. No state hint: match the district name across all states. Infer a state only when the
         district name is unique to one state; if it is ambiguous across states (e.g. "Ramnagar"
         in both Bihar and Karnataka), return unmapped (state/district null) so the record routes
         to the universal unmapped table instead of being guessed by list order.
      3. No district match: fall back to a bare state pattern match if present.
    """
    result: Dict[str, Optional[str]] = {'state': None, 'district': None, 'organisation': None}

    if not location_text or not mapping_data:
        return result

    location_text = _normalize_text(location_text)
    states: List[Dict] = mapping_data.get('states', [])

    # Pass 1: detect state hint in text
    matched_state = None
    for state in states:
        if _match_patterns(state['matching_patterns'], location_text):
            matched_state = state
            break

    # Pass 2a: state found — restrict district search to that state
    if matched_state:
        for district in matched_state['districts']:
            if _match_patterns(district['matching_patterns'], location_text):
                result['state'] = matched_state['name']
                result['district'] = district['name']
                result['organisation'] = district.get('organisation')
                return result
        result['state'] = matched_state['name']
        return result

    # Pass 2b: no state hint. Collect every (state, district) whose district pattern matches,
    # across all states, then only infer a state when the district name is UNIQUE. If the same
    # district name exists in more than one state (e.g. "Ramnagar" in both Bihar and Karnataka),
    # we cannot safely pick one without a state hint, so we leave state/district null and let the
    # record route to the universal unmapped table. The previous code returned the first match by
    # `states` list order, which silently mis-mapped ambiguous districts (issue 9).
    district_matches = []
    for state in states:
        for district in state['districts']:
            if _match_patterns(district['matching_patterns'], location_text):
                district_matches.append((state, district))

    distinct_states = {state['name'] for state, _ in district_matches}
    if len(district_matches) >= 1 and len(distinct_states) == 1:
        # Unambiguous: exactly one state owns the matched district name(s).
        state, district = district_matches[0]
        result['state'] = state['name']
        result['district'] = district['name']
        result['organisation'] = district.get('organisation')
        return result
    elif len(distinct_states) > 1:
        # Ambiguous across states with no state hint -> unmapped (leave state/district null).
        district_name = district_matches[0][1]['name']
        logger.warning(
            "Ambiguous district '%s' matched in multiple states %s with no state hint in "
            "location='%s'; routing to unmapped (issue 9).",
            district_name, sorted(distinct_states), location_text,
        )
        return result

    # Pass 3: no district match at all -> fall back to a bare state pattern match if present.
    for state in states:
        if _match_patterns(state['matching_patterns'], location_text):
            result['state'] = state['name']
            return result

    return result


def _match_organisation(org_text: str, mapping_data: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """
    Layer 3: match organisation name → infer state + district from org mapping.
    Only called when location-based matching yields no result.
    """
    result: Dict[str, Optional[str]] = {'state': None, 'district': None, 'organisation': None}

    if not org_text or not mapping_data:
        return result

    org_text = _normalize_text(org_text)
    org_to_districts: Dict[str, List[str]] = mapping_data.get('organisation_to_district_mapping', {})
    district_to_org: Dict[str, Dict[str, Optional[str]]] = mapping_data.get('district_to_organisation_mapping', {})

    for org_name in org_to_districts:
        if _matches(org_name, org_text):
            result['organisation'] = org_name
            for state_name, districts_map in district_to_org.items():
                org_districts = [d for d, o in districts_map.items() if o == org_name]
                if org_districts:
                    result['state'] = state_name
                    if len(org_districts) == 1:
                        result['district'] = org_districts[0]
                    break
            return result

    return result


def categorise_from_text(text: str, mapping_data: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """
    Categorise a single free-text string through all three layers.
    Treats the entire text as location context (no org-only layer separation).
    Prefer categorise_story() for Story objects to keep location and org separate.
    """
    result = _match_location(text, mapping_data)
    if result['state'] or result['district']:
        return result
    return _match_organisation(text, mapping_data)


def categorise_story(story: Story, mapping_data: Dict[str, Any]) -> Optional[Dict[str, Optional[str]]]:
    """
    Categorise a story using a strict field-priority order:

      1. other_params['location'] (fallback: story.location) — district then state matching
      2. other_params['organization'] — org matching, only when step 1 finds nothing

    Returns None when no usable text exists.
    """
    location_text: Optional[str] = None
    if story.other_params:
        english_json = story.other_params.get('english_json')
        if isinstance(english_json, dict):
            val = english_json.get('location')
            if val and isinstance(val, str):
                location_text = val
        if not location_text:
            val = story.other_params.get('location')
            if val and isinstance(val, str):
                location_text = val
    if not location_text and story.location:
        location_text = story.location

    if location_text and not _is_likely_english(location_text):
        logger.info(f"Story {story.id}: location '{location_text}' appears non-English")
        english_json_loc = None
        if story.other_params:
            english_json = story.other_params.get('english_json')
            if isinstance(english_json, dict):
                val = english_json.get('location')
                if val and isinstance(val, str) and _is_likely_english(val):
                    english_json_loc = val
                    logger.info(f"Story {story.id}: using existing english_json.location '{english_json_loc}'")
        if english_json_loc:
            location_text = english_json_loc
        else:
            translated = _translate_location_to_english(location_text)
            if translated:
                if story.other_params is None:
                    story.other_params = {}
                if not isinstance(story.other_params.get('english_json'), dict):
                    story.other_params['english_json'] = {}
                story.other_params['english_json']['location'] = translated
                story.save(update_fields=['other_params'])
                logger.info(f"Story {story.id}: persisted translated location '{translated}' to english_json")
                location_text = translated
            else:
                logger.warning(f"Story {story.id}: translation unavailable; proceeding with original text '{location_text}'")

    org_text: Optional[str] = None
    if story.other_params:
        english_json = story.other_params.get('english_json')
        if isinstance(english_json, dict):
            val = english_json.get('organization')
            if val and isinstance(val, str):
                org_text = val
        if not org_text:
            val = story.other_params.get('organization')
            if val and isinstance(val, str):
                org_text = val

    if not location_text and not org_text:
        return None

    if location_text:
        result = _match_location(location_text, mapping_data)
        if result['state'] or result['district']:
            return result

    if org_text:
        return _match_organisation(org_text, mapping_data)

    return {'state': None, 'district': None, 'organisation': None}


# -------------- DB UPDATE ------------------

def update_stories_in_db(updates: List[Dict[str, Any]]) -> None:
    try:
        with transaction.atomic():
            for update in updates:
                story_id = update['story_id']
                try:
                    story = Story.objects.get(id=story_id)
                    story.state = update['state']
                    story.district = update['district']
                    story.save(update_fields=['state', 'district'])
                    logger.info(f"Updated story {story_id}: state={update['state']}, district={update['district']}")
                except Story.DoesNotExist:
                    logger.info(f"Story {story_id} not found")
                except Exception as e:
                    logger.error(f"Error updating story {story_id}: {e}")
    except Exception as e:
        logger.error(f"Database transaction error: {e}")


# -------------- MAIN ------------------

def run_state_categorisation(story_queryset=None, mapping_data: Dict[str, Any] = None, company_bot=None) -> Dict[str, Any]:
    """
    Main entry point. Processes guest-discussion stories missing state or district.
    mapping_data source: explicit mapping_data > company_bot.dynamic_context.
    """
    if mapping_data is None:
        if company_bot is None:
            logger.error("company_bot required for mapping data; aborting state categorisation")
            return {'processed': 0, 'skipped': 0, 'failed': []}
        mapping_data = load_mapping_from_bot(company_bot)

    if not mapping_data:
        logger.error("No mapping data available; aborting state categorisation")
        return {'processed': 0, 'skipped': 0, 'failed': []}

    if story_queryset is None:
        story_queryset = Story.objects.filter(
            other_params__flow='guest-discussion',
        ).filter(
            Q(state__isnull=True) | Q(district__isnull=True)
        )

    stories = list(story_queryset)
    logger.info(f"Starting state categorisation for {len(stories)} stories")

    processed = 0
    skipped = 0
    failed: List[int] = []

    for story in stories:
        try:
            categorisation = categorise_story(story, mapping_data)
            if categorisation is None:
                skipped += 1
                continue

            if categorisation['state'] or categorisation['district']:
                story.state = categorisation['state']
                story.district = categorisation['district']
                story.save(update_fields=['state', 'district'])
                logger.info(f"Updated story {story.id}: state={categorisation['state']}, district={categorisation['district']}")
                processed += 1
            else:
                skipped += 1

        except Exception as e:
            logger.error(f"Error categorising story {story.id}: {e}")
            failed.append(story.id)

    summary = {
        'total': len(stories),
        'processed': processed,
        'skipped': skipped,
        'failed': failed,
    }

    logger.info(
        f"State categorisation complete — "
        f"processed: {summary['processed']}, skipped: {summary['skipped']}, failed: {len(failed)}"
    )
    return summary


# -------------- CRON ENTRY POINT ------------------

def run_cron() -> None:
    from chatbot.models import CompanyBot
    try:
        bot = CompanyBot.objects.get(route='/state-classification-guest-discussion')
    except CompanyBot.DoesNotExist:
        logger.error("CompanyBot with route '/state-classification-guest-discussion' not found")
        return
    except CompanyBot.MultipleObjectsReturned:
        logger.error("Multiple CompanyBots with route '/state-classification-guest-discussion' found")
        return
    run_state_categorisation(company_bot=bot)


# -------------- CONVENIENCE ENTRY POINTS ------------------

def backfill_state_from_district(mapping_data: Dict[str, Any] = None, company_bot=None) -> Dict[str, Any]:
    """
    Backfill state for stories that already have a district set but are missing a state.

    This can happen when older scripts or imports populated the district field without
    setting the state. We infer the correct state from district_to_organisation_mapping.
    """
    if mapping_data is None:
        if company_bot is None:
            logger.error("company_bot required for mapping data; aborting state backfill")
            return {'processed': 0, 'skipped': 0, 'failed': []}
        mapping_data = load_mapping_from_bot(company_bot)

    if not mapping_data:
        logger.error("No mapping data available; aborting state backfill")
        return {'processed': 0, 'skipped': 0, 'failed': []}

    # Build district → state lookup from the mapping
    district_to_state: Dict[str, str] = {}
    for state_name, districts_map in mapping_data.get('district_to_organisation_mapping', {}).items():
        for district_name in districts_map:
            district_to_state[district_name] = state_name

    stories = list(
        Story.objects.filter(state__isnull=True)
        .exclude(district__isnull=True)
        .exclude(district__exact='')
    )

    logger.info(f"Starting state backfill for {len(stories)} stories with district but no state")

    updates: List[Dict[str, Any]] = []
    skipped = 0
    failed: List[int] = []

    for story in stories:
        try:
            inferred_state = district_to_state.get(story.district)
            if inferred_state:
                updates.append({
                    'story_id': story.id,
                    'state': inferred_state,
                    'district': story.district,
                })
            else:
                logger.warning(f"Story {story.id}: district '{story.district}' not found in mapping")
                skipped += 1
        except Exception as e:
            logger.error(f"Error backfilling story {story.id}: {e}")
            failed.append(story.id)

    update_stories_in_db(updates)

    summary = {
        'total': len(stories),
        'processed': len(updates),
        'skipped': skipped,
        'failed': failed,
    }

    logger.info(
        f"State backfill complete — "
        f"processed: {summary['processed']}, skipped: {summary['skipped']}, failed: {len(failed)}"
    )
    return summary


def run_for_story_ids(story_ids: List[int], mapping_data: Dict[str, Any] = None, company_bot=None) -> Dict[str, Any]:
    stories = Story.objects.filter(id__in=story_ids)
    return run_state_categorisation(story_queryset=stories, mapping_data=mapping_data, company_bot=company_bot)


def run_for_date_range(start_date, end_date, mapping_data: Dict[str, Any] = None, company_bot=None) -> Dict[str, Any]:
    stories = Story.objects.filter(
        created_at__gte=start_date,
        created_at__lte=end_date,
        state__isnull=True,
    )
    return run_state_categorisation(story_queryset=stories, mapping_data=mapping_data, company_bot=company_bot)