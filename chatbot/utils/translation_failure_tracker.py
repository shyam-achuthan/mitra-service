"""
Thread-local tracking of translation failures during story creation.

Historically, when a translation call failed (a non-200 response, or an exception), the
translation helpers silently returned the original vernacular text, which was then stored into
the English fields of a Story that was still marked COMPLETED. The failure produced success-shaped
data with no marker, so the report looked finished while its English fields actually held
vernacular text, and no logs identified it (issue 3, Defect A).

This module lets the low-level translation helpers signal a failure without changing their return
type or every caller's signature. Story-building code opens a tracking scope, the helpers flag any
failure into thread-local state, and the builder inspects the flag before marking the story
COMPLETED so it can record that translation was incomplete (and therefore reprocessable) instead of
silently passing bad data through.

Thread-local is appropriate because a single story is built synchronously within one Celery task
(one thread); scopes do not overlap across stories on the same thread.
"""
import threading

_state = threading.local()


def start_scope():
    """Begin a fresh translation-tracking scope for building one story."""
    _state.failed = False
    _state.count = 0


def record_failure():
    """Flag that a translation call failed within the current scope."""
    # Tolerate being called outside a scope (e.g. translation used elsewhere): initialize lazily.
    if not hasattr(_state, 'failed'):
        _state.failed = False
        _state.count = 0
    _state.failed = True
    _state.count += 1


def had_failure():
    """Return True if any translation failure was recorded in the current scope."""
    return getattr(_state, 'failed', False)


def failure_count():
    """Return the number of translation failures recorded in the current scope."""
    return getattr(_state, 'count', 0)


def clear_scope():
    """Reset the tracking state at the end of building one story."""
    _state.failed = False
    _state.count = 0
