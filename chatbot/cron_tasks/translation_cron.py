import logging
from django.utils import timezone
from chatbot.scripts.guest_discussion.output.update_non_english_story import fix_guest_discussion_stories
from chatbot.scripts.mi_guest_flow.output.update_non_english_story import fix_guest_mi_story_stories
import os

logger = logging.getLogger('django')


def _summarize(label, result):
    """
    Log a per-run summary for one flow so cron outcomes are observable, and surface anomalies.

    Each fix_* function returns {'fixed': [...], 'failed': [...], 'skipped': [...], 'total': int}.
    We log the counts at INFO, and if any story failed to be fixed we log at ERROR so the run is
    flagged for the dev team instead of the failure sitting silently in a file nobody watches.
    Returns the count dict so the caller can aggregate.
    """
    if not isinstance(result, dict):
        logger.error("[TRANSLATION CRON] %s returned no summary (result=%r)", label, result)
        return {'fixed': 0, 'failed': 0, 'skipped': 0, 'total': 0}

    def _count(value):
        # The fix_* functions are inconsistent: some return integer counts (Guest MI Story),
        # others may return lists of records. Accept either shape so this never throws.
        if isinstance(value, int):
            return value
        if isinstance(value, (list, tuple)):
            return len(value)
        return 0

    counts = {
        'fixed': _count(result.get('fixed', 0)),
        'failed': _count(result.get('failed', 0)),
        'skipped': _count(result.get('skipped', 0)),
        'total': result.get('total', 0),
    }
    logger.info(
        "[TRANSLATION CRON] %s summary: total=%s fixed=%s failed=%s skipped=%s",
        label, counts['total'], counts['fixed'], counts['failed'], counts['skipped'],
    )
    if counts['failed']:
        # Only a list-shaped 'failed' carries per-story detail; an integer count has none.
        raw_failed = result.get('failed')
        failed_ids = (
            [f.get('story_id') for f in raw_failed if isinstance(f, dict)]
            if isinstance(raw_failed, (list, tuple)) else []
        )
        logger.error(
            "[TRANSLATION CRON] %s had %s FAILED stor(y/ies) needing attention: %s",
            label, counts['failed'], failed_ids,
        )
    return counts


def handle_non_english_fix_cron():
    """
    Cron job to fix non-English content in Guest Discussion and Guest MI Story flows.
    """
    started_at = timezone.now()
    try:
        logger.info('=' * 70)
        logger.info('🌐 Starting Non-English Content Fix Cron at: {}'.format(started_at))
        print(f"Cron running from cwd: {os.getcwd()}")
        logger.info(f"Cron running from cwd: {os.getcwd()}")

        logger.info('🔧 Starting Guest Discussion non-English fix...')
        discussion_result = fix_guest_discussion_stories()
        discussion_counts = _summarize('Guest Discussion', discussion_result)

        # Fix Guest MI Story stories
        logger.info('🔧 Starting Guest MI Story non-English fix...')
        mi_story_result = fix_guest_mi_story_stories()
        mi_counts = _summarize('Guest MI Story', mi_story_result)

        # Aggregate run metrics so a single line captures the whole cron outcome.
        total_fixed = discussion_counts['fixed'] + mi_counts['fixed']
        total_failed = discussion_counts['failed'] + mi_counts['failed']
        total_seen = discussion_counts['total'] + mi_counts['total']
        logger.info('=' * 70)
        logger.info(
            "[TRANSLATION CRON] Run complete in %ss: seen=%s fixed=%s failed=%s",
            (timezone.now() - started_at).total_seconds(), total_seen, total_fixed, total_failed,
        )
        if total_failed:
            logger.error(
                "[TRANSLATION CRON] Run finished with %s failed stor(y/ies) across flows; needs review.",
                total_failed,
            )
        logger.info('Non-English Content Fix Cron completed successfully at: {}'.format(timezone.now()))

    except Exception as e:
        logger.exception("❌ Error during non-English content fix cron: %s", str(e))
