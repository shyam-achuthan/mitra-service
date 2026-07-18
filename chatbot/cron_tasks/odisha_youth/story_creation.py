from chatbot.llm_models.llm_script import handle_bedrock_model, handle_openai_model
from chatbot.models import ChatSession, CompanyBot, CompanyChat, Story, ChatStatus
from chatbot.models.enums import LLMProvider, StoryStatusChoices
from chatbot.cron_tasks.story_generation_tracking import exclude_exhausted
from chatbot.utils.story_utils.story_utils import generate_story
from datetime import timedelta
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
import json
import logging
import re


logger = logging.getLogger('django')

ODISHA_YOUTH_SESSION_TYPE = 'odisha-youth'
ODISHA_YOUTH_BOT_ROUTE = '/story-creation-odisha-youth-bot'
STORY_LLM_MAX_ATTEMPTS = 3


def _story_response_has_title(response):
    """True if the LLM returned a dict with a non-empty title."""
    if not isinstance(response, dict):
        return False
    title = response.get('title')
    return title is not None and str(title).strip() != ''


def chat_sessions_without_story(session_type=ODISHA_YOUTH_SESSION_TYPE):
    """
    ChatSession rows of this session_type that have no Story with the same `session`
    string (Story links via Story.session, not a FK on ChatSession).

    Eligibility by age, to avoid picking up sessions that are still in progress:
      - sessions older than 30 minutes are included regardless of session_status, and
      - sessions newer than 30 minutes are included only when session_status is COMPLETED.
    Sessions already marked story_generation_exhausted are excluded (see
    story_generation_tracking) so persistently-failing sessions are not retried forever.
    Pass session_type=None to include all session types.
    """
    linked_story = Story.objects.filter(session=OuterRef('session'))
    half_hour_ago = timezone.now() - timedelta(minutes=30)
    qs = ChatSession.objects.filter(
        ~Exists(linked_story),
        session_type=session_type,
    ).filter(
        Q(created_at__lte=half_hour_ago)
        | Q(created_at__gte=half_hour_ago, session_status=ChatStatus.COMPLETED)
    )
    return exclude_exhausted(qs)


def chat_session_ids_without_story(session_type=ODISHA_YOUTH_SESSION_TYPE):
    """`session` values (string ids) for ChatSessions that have no matching Story."""
    return chat_sessions_without_story(session_type=session_type).values_list('session', flat=True)


def _build_chat_transcript(company_chats):
    transcript_parts = []
    question_index = 0
    answer_index = 0
    for chat in company_chats:
        if chat.sender_id == 1:
            question_index += 1
            message = chat.message
            if chat.translated_message is not None and chat.translated_message != '':
                message = chat.translated_message
            transcript_parts.append(
                f"<question_{question_index}>\n{message}\n</question_{question_index}>"
            )
        else:
            answer_index += 1
            message = chat.message
            if chat.translated_message is not None and chat.translated_message != '':
                message = chat.translated_message
            transcript_parts.append(
                f"<answer_{answer_index}>\n{message}\n</answer_{answer_index}>"
            )
    return '\n'.join(transcript_parts)


def _build_messages(company_bot, company_chats):
    transcript = _build_chat_transcript(company_chats=company_chats)
    if company_bot.provider == LLMProvider.BEDROCK_CONVERSE:
        return [
            {
                'role': 'user',
                'content': [{'text': transcript}]
            },
            {
                'role': 'assistant',
                'content': [{'text': "```json"}]
            }
        ]
    return [{
        'role': 'user',
        'content': transcript
    }]


def _get_system_prompt(company_bot):
    prompt_parts = [company_bot.context, company_bot.end_context]
    prompt_text = '\n\n'.join([part for part in prompt_parts if part and part.strip()])
    if company_bot.provider == LLMProvider.BEDROCK_CONVERSE:
        return [{'text': prompt_text}] if prompt_text else None
    return prompt_text


def _call_story_llm(company_bot, messages, system_prompt):
    """Single LLM invocation for story JSON (no retries)."""
    if company_bot.provider == LLMProvider.BEDROCK_CONVERSE:
        print("messages: ", messages)
        print("system_prompt: ", system_prompt)
        return handle_bedrock_model(
            company_bot=company_bot,
            system_prompt=system_prompt,
            messages=messages,
            max_token=company_bot.max_token,
            temperature=company_bot.bot_temperature,
            top_p=company_bot.filter_score,
            model_name=company_bot.llm_model,
            is_json_response=False,
            stop_sequences=["```"],
        )

    openai_messages = messages
    if system_prompt:
        openai_messages = [{'role': 'system', 'content': system_prompt}] + messages
    return handle_openai_model(
        company_bot=company_bot,
        messages=openai_messages,
        max_token=company_bot.max_token,
        temperature=company_bot.bot_temperature,
        model_name=company_bot.llm_model,
        top_p=company_bot.filter_score,
        is_json_response=False,
        key_name=company_bot.llm_key or 'OPENAI_API_KEY',
        is_actual_key=bool(company_bot.provider_keys),
    )


def _generate_story_for_session(company_bot, session):
    company_chats = CompanyChat.objects.filter(session=session).order_by('created_at')
    if not company_chats.exists():
        logger.info('No chats found for session=%s', session)
        return None

    messages = _build_messages(company_bot=company_bot, company_chats=company_chats)
    system_prompt = _get_system_prompt(company_bot)

    if company_bot.provider not in (
        LLMProvider.BEDROCK_CONVERSE,
        LLMProvider.OPENAI,
    ):
        logger.warning('Unsupported provider=%s for bot=%s', company_bot.provider, company_bot.id)
        return None

    for attempt in range(1, STORY_LLM_MAX_ATTEMPTS + 1):
        try:
            response = _call_story_llm(company_bot, messages, system_prompt)
        except Exception as e:
            log_fn = logger.error if attempt == STORY_LLM_MAX_ATTEMPTS else logger.warning
            log_fn(
                'Story LLM call failed session=%s attempt=%s/%s: %s',
                session,
                attempt,
                STORY_LLM_MAX_ATTEMPTS,
                e,
                exc_info=(attempt == STORY_LLM_MAX_ATTEMPTS),
            )
            continue

        if _story_response_has_title(response):
            return response

        logger.warning(
            'Story LLM missing or empty title session=%s attempt=%s/%s response=%s',
            session,
            attempt,
            STORY_LLM_MAX_ATTEMPTS,
            response,
        )

    return None


def _persist_story_from_llm_response(session_id, response):
    """
    Save LLM story JSON: title on Story.title, remaining keys in Story.other_params,
    session id on Story.session, author from ChatSession.profile when present.
    """
    title = str(response['title']).strip()
    other_params = {k: v for k, v in response.items() if k != 'title'}
    chat_session = (
        ChatSession.objects.filter(session=session_id)
        .select_related('profile')
        .first()
    )
    author = chat_session.profile if chat_session else None
    return Story.objects.create(
        session=session_id,
        title=title,
        other_params=other_params,
        author=author,
        client_created_at=chat_session.created_at if chat_session else None,
        stage=StoryStatusChoices.COMPLETED
    )


def _get_location_text(session):
    chats = CompanyChat.objects.filter(
        session=session,
        stage__in=['STATE_2', 'DISTRICT_LOCATION'],
        receiver_id=1,
    ).order_by('created_at')
    parts = []
    for chat in chats:
        text = chat.translated_message if chat.translated_message else chat.message
        if text:
            parts.append(text)
    return ' '.join(parts) if parts else None


def _normalize_text(text):
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _no_match_result():
    return {
        'Matched Village': None,
        'Matched Block': None,
        'Matched District': None,
        'Match Confidence': 'no_match',
        'Matched Pattern': None,
        'Match Level': 'no_match',
    }


def _classify_location(text, classification_data):
    normalized = _normalize_text(text)

    for entry in classification_data.get('villages', []):
        for pattern in entry.get('patterns', []):
            if pattern in normalized:
                return {
                    'Matched Village': entry['village'],
                    'Matched Block': entry['block'],
                    'Matched District': entry['district'],
                    'Match Confidence': 'high',
                    'Matched Pattern': pattern,
                    'Match Level': 'village',
                }

    for entry in classification_data.get('block_patterns', []):
        for pattern in entry.get('patterns', []):
            if pattern in normalized:
                return {
                    'Matched Village': None,
                    'Matched Block': entry['block'],
                    'Matched District': entry['district'],
                    'Match Confidence': 'medium',
                    'Matched Pattern': pattern,
                    'Match Level': 'block',
                }

    for entry in classification_data.get('district_patterns', []):
        for pattern in entry.get('patterns', []):
            if pattern in normalized:
                return {
                    'Matched Village': None,
                    'Matched Block': None,
                    'Matched District': entry['district'],
                    'Match Confidence': 'low',
                    'Matched Pattern': pattern,
                    'Match Level': 'district',
                }

    return _no_match_result()


def _apply_location_classification(story, session, company_bot):
    if not company_bot.dynamic_context:
        return
    try:
        classification_data = json.loads(company_bot.dynamic_context)
    except (json.JSONDecodeError, TypeError):
        logger.warning('Invalid dynamic_context JSON for bot=%s', company_bot.id)
        return

    location_text = _get_location_text(session)
    if not location_text:
        logger.info('No location text found for session=%s', session)

    result = _classify_location(location_text, classification_data) if location_text else _no_match_result()

    other_params = story.other_params or {}
    other_params.update(result)
    story.other_params = other_params
    story.save(update_fields=['other_params'])
    logger.info(
        'Location classified session=%s level=%s village=%s district=%s',
        session,
        result.get('Match Level'),
        result.get('Matched Village'),
        result.get('Matched District'),
    )


def create_story():
    try:
        company_bot = CompanyBot.objects.filter(route=ODISHA_YOUTH_BOT_ROUTE).first()
        if not company_bot:
            logger.error('No CompanyBot found for route=%s', ODISHA_YOUTH_BOT_ROUTE)
            return

        odisha_session_ids = ChatSession.objects.filter(
            session_type=ODISHA_YOUTH_SESSION_TYPE
        ).values('session')

        stories_missing_location = Story.objects.filter(
            session__in=odisha_session_ids
        ).filter(
            Q(other_params__isnull=True) | ~Q(other_params__has_key='Match Level')
        ).select_related('author')

        logger.info(
            'Processing %s Odisha Youth stories missing location classification',
            stories_missing_location.count(),
        )

        for story in stories_missing_location:
            chat_session = ChatSession.objects.filter(session=story.session).first()
            if not chat_session:
                logger.warning('No ChatSession for story id=%s session=%s', story.id, story.session)
                continue

            language = chat_session.language if chat_session.language else 'en'
            profile_id = story.author_id

            _apply_location_classification(story, story.session, company_bot)

            try:
                story_id, story_content, error_message, error_type = generate_story(
                    profile_id=profile_id,
                    session=story.session,
                    access_token=None,
                    flow=ODISHA_YOUTH_SESSION_TYPE,
                    language=language,
                )
                if error_message:
                    logger.error(
                        'generate_story failed story_id=%s session=%s error_type=%s error=%s',
                        story.id, story.session, error_type, error_message,
                    )
                else:
                    logger.info(
                        'generate_story completed story_id=%s session=%s new_story_id=%s',
                        story.id, story.session, story_id,
                    )
            except Exception as e:
                logger.error(
                    'generate_story exception story_id=%s session=%s: %s',
                    story.id, story.session, e,
                    exc_info=True,
                )

    except Exception as e:
        logger.error('Error creating story for Odisha Youth: %s', e)