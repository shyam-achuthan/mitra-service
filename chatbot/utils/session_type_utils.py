"""
Shared resolution of a validated `ChatSession.session_type` value.

Historically each WebSocket consumer wrote `session_type` differently: some hard-coded a
`ChatType` constant, while the generalized consumers stored the client-supplied `flow_name`
verbatim. Because `flow_name` arrives in the authenticate frame with no validation and no lookup
against configuration, the stored value could be any arbitrary string, including values that are
not members of the `ChatType` enum (e.g. `guest_discussion`). That is the root cause behind the
Metabase mapping breakage in issues 1, 2 and 5.

This module centralizes the decision so every consumer resolves `session_type` the same way:
a recognized value is normalized to its canonical `ChatType`, and an unrecognized value falls
back to a safe default instead of being stored blindly. The write path can no longer persist an
invalid, client-controlled classification.

NOTE: the exact canonical string for the chaupal / guest-discussion flow must stay aligned with
the Metabase queries (see improvements-todo-2.0.0.md, Issue 1 COORD item). This helper keeps that
decision in one place; changing the alias map here is the single point of change.
"""
import logging

from chatbot.models.enums import ChatType

logger = logging.getLogger('django')

# Set of valid ChatType values, for O(1) membership checks.
_VALID_SESSION_TYPES = {choice.value for choice in ChatType}

# Aliases the client may send that are not themselves valid ChatType values, mapped to the
# canonical ChatType value they represent. This is the single point of alignment with the
# dashboards: the chaupal / guest-discussion flow is normalized to one canonical value.
_SESSION_TYPE_ALIASES = {
    'guest_discussion': ChatType.shikshaChaupal.value,
    'guest-discussion': ChatType.shikshaChaupal.value,
}


def resolve_session_type(flow_name, default=ChatType.shikshaChaupal.value):
    """
    Resolve a client-supplied `flow_name` into a validated `session_type`.

    - If `flow_name` is already a valid `ChatType` value, it is used as-is.
    - If `flow_name` matches a known alias, it is normalized to the canonical `ChatType` value.
    - Otherwise a warning is logged and `default` is returned, so an unrecognized or missing
      client value can never be stored verbatim as `session_type`.

    Args:
        flow_name: the raw value from the client authenticate frame (may be None or arbitrary).
        default: the canonical value to fall back to when `flow_name` is not recognized.

    Returns:
        A validated `session_type` string.
    """
    if flow_name in _VALID_SESSION_TYPES:
        return flow_name

    normalized = _SESSION_TYPE_ALIASES.get(flow_name)
    if normalized is not None:
        logger.info(
            "Normalized client flow_name '%s' to canonical session_type '%s'",
            flow_name, normalized,
        )
        return normalized

    logger.warning(
        "Unrecognized client flow_name '%s' for session_type; falling back to default '%s'. "
        "The client value was NOT stored verbatim.",
        flow_name, default,
    )
    return default
