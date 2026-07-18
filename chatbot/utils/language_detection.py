"""
Shared, whitelist-free detection of non-English (non-Latin-script) text.

The story-fix scripts historically each defined their own copy of a regex whitelist of Unicode
script ranges (Latin, Latin-supplement, Devanagari, and later Kannada) and treated text as
non-English only if it contained a character from that hardcoded list. Any supported language not in
the list (Telugu, Odia, Tamil, ...) was silently skipped, which is exactly how Kannada reports went
untranslated (issue 4). The regex was also duplicated across four files and had already drifted out
of sync.

This module centralizes the check and replaces the whitelist with a positive test: text is
non-English when it contains any non-ASCII letter. That covers every non-Latin script without
enumerating ranges, so adding a new supported language cannot silently misclassify text again.
"""


def has_non_ascii_letter(text):
    """Return True if `text` contains at least one non-ASCII letter (any non-Latin script)."""
    if not text or not isinstance(text, str):
        return False
    return any(ch.isalpha() and ord(ch) > 127 for ch in text)


def has_ascii_letter(text):
    """Return True if `text` contains at least one A-Z / a-z letter."""
    if not text or not isinstance(text, str):
        return False
    return any('a' <= ch.lower() <= 'z' for ch in text)


def is_non_english_text(text):
    """
    Return True when `text` should be treated as non-English (contains non-Latin script).

    Whitelist-free: any non-ASCII letter marks the text as non-English. Text that is empty, or
    contains only ASCII letters, numbers, or symbols, is treated as English (nothing to translate).
    This replaces the previous per-file whitelist that silently skipped un-listed scripts.
    """
    if not text or not isinstance(text, str):
        return False
    return has_non_ascii_letter(text)
