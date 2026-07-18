"""
Helpers for anchoring a Story to when the underlying discussion actually happened.

`Story.created_at` is the insert time of the story row, which is often much later than the
conversation it summarizes: nightly crons and one-off backfills create stories long after the
session. Filtering dashboards by `Story.created_at` therefore produces period counts that disagree
with counts filtered by the session's timestamp (issue 6), and a backfill can make counts jump.

`Story.client_created_at` is the field meant to carry the true origin time, but historically it was
only populated during data migration. These helpers let story-creation code populate it from the
originating ChatSession so every new story carries a stable, backfill-proof anchor, letting the
dashboards filter on a single story-side column with period-correct results.
"""
import logging

from chatbot.models import ChatSession

logger = logging.getLogger('django')


def resolve_session_created_at(session):
    """
    Return the origin timestamp for a story: the ChatSession.created_at for the given session id.

    `session` is the session string (the UUID join key), matching ChatSession.session. Returns None
    if no session is found, so callers can leave client_created_at unset rather than guessing.
    """
    if not session:
        return None
    chat_session = ChatSession.objects.filter(session=session).only('created_at').first()
    if chat_session is None:
        logger.info("No ChatSession found for session=%s while resolving story origin timestamp", session)
        return None
    return chat_session.created_at


def set_client_created_at_if_missing(story_fields, session):
    """
    Set client_created_at in a story-fields dict from the session origin time, only if not already
    present/truthy. Mutates and returns the dict. Never overwrites an existing value, so it is safe
    to call on both new and existing stories.
    """
    if story_fields.get('client_created_at'):
        return story_fields
    origin = resolve_session_created_at(session)
    if origin is not None:
        story_fields['client_created_at'] = origin
    return story_fields
