"""
Shared tracking of story-generation outcomes for the pilot cron jobs.

The pilot story-creation crons select every session that lacks a Story and try to generate one.
When generation fails, the previous code logged an error and moved on, leaving the session in the
"missing story" set so the next nightly run retried it, failed again, and skipped again forever
(issue 7). Nothing recorded why a session had no story, so the sessions-vs-story gap could not be
told apart into "genuinely empty" vs "we keep failing to generate this one".

This module makes the outcome explicit and bounded, without any schema change (state lives in the
existing ChatSession.other_params JSON field):

- record a failure reason and an attempt count on the session, and
- once attempts reach a cap, stop re-selecting that session so the retry loop terminates and the
  session is left in a queryable "generation exhausted" state for review instead of retried nightly.

All state is future-only and additive; existing rows are untouched until a cron next processes them.
"""
import logging

from chatbot.models import ChatSession

logger = logging.getLogger('django')

# Keys used inside ChatSession.other_params to track generation outcome.
ATTEMPTS_KEY = 'story_generation_attempts'
REASON_KEY = 'no_story_reason'
EXHAUSTED_KEY = 'story_generation_exhausted'

# After this many failed cron passes for a session, stop retrying it and mark it exhausted.
MAX_GENERATION_PASSES = 3


def record_generation_failure(session, reason):
    """
    Record that story generation failed for `session` (the session string id).

    Increments the per-session attempt count and stores a human-readable reason in
    ChatSession.other_params. When the attempt count reaches MAX_GENERATION_PASSES, marks the
    session exhausted so exhausted_session_ids() will exclude it from future selection. Returns the
    new attempt count, or None if the session row is not found.
    """
    chat_session = ChatSession.objects.filter(session=session).first()
    if chat_session is None:
        logger.warning("Cannot record story-generation failure: no ChatSession for session=%s", session)
        return None

    params = dict(chat_session.other_params or {})
    attempts = int(params.get(ATTEMPTS_KEY, 0)) + 1
    params[ATTEMPTS_KEY] = attempts
    params[REASON_KEY] = reason
    if attempts >= MAX_GENERATION_PASSES:
        params[EXHAUSTED_KEY] = True
        logger.error(
            "Story generation exhausted for session=%s after %s passes (reason=%s); "
            "will no longer be retried and needs review.",
            session, attempts, reason,
        )
    else:
        logger.warning(
            "Story generation failed for session=%s (attempt %s/%s, reason=%s); will retry.",
            session, attempts, MAX_GENERATION_PASSES, reason,
        )

    chat_session.other_params = params
    chat_session.save(update_fields=['other_params'])
    return attempts


def clear_generation_failure(session):
    """
    Clear any recorded failure markers for `session`, e.g. after a story is successfully created.
    No-op if the session is not found or has no markers.
    """
    chat_session = ChatSession.objects.filter(session=session).first()
    if chat_session is None or not chat_session.other_params:
        return
    params = dict(chat_session.other_params)
    changed = False
    for key in (ATTEMPTS_KEY, REASON_KEY, EXHAUSTED_KEY):
        if key in params:
            params.pop(key, None)
            changed = True
    if changed:
        chat_session.other_params = params
        chat_session.save(update_fields=['other_params'])


def exclude_exhausted(queryset):
    """
    Given a ChatSession queryset, exclude sessions already marked generation-exhausted so the cron
    stops retrying them. Uses the other_params JSON marker, so no schema change is required.
    """
    return queryset.exclude(**{f'other_params__{EXHAUSTED_KEY}': True})
