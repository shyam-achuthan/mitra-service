"""
Shared, whitelist-free detection of non-English (non-Latin-script) text.

The story-fix scripts historically each defined their own copy of a regex whitelist of Unicode
script ranges (Latin, Latin-supplement, Devanagari, and later Kannada) and treated text as
non-English only if it contained a character from that hardcoded list. Any supported language not in
the list (Telugu, Odia, Tamil, ...) was silently skipped, which is exactly how Kannada reports went
untranslated (issue 4). The regex was also duplicated across four files and had already drifted out
of sync.

This module centralizes the check and replaces the whitelist with a positive test based on Unicode
script rather than a hardcoded range list: text is non-English when it contains a letter that belongs
to a non-Latin script. That covers every non-Latin script (Devanagari, Kannada, Telugu, Odia, Tamil,
...) without enumerating ranges, so adding a new supported language cannot silently misclassify text
again. Crucially, accented Latin letters (cafe, Jose, Straße, resume) are treated as English, because
they are still Latin script; a bare "any non-ASCII letter" test would wrongly flag them.
"""
import unicodedata


def _is_non_latin_letter(ch):
    """
    Return True if `ch` is an alphabetic character that is NOT Latin script.

    ASCII letters are Latin by definition. For non-ASCII letters we consult the Unicode character
    name: every Latin-script letter, accented or not, has a name beginning with "LATIN" (e.g.
    "LATIN SMALL LETTER E WITH ACUTE"), so a letter whose name does not start with "LATIN" belongs
    to another script (Devanagari, Kannada, Telugu, ...).
    """
    if not ch.isalpha():
        return False
    if ord(ch) < 128:
        return False  # plain ASCII letter -> Latin
    name = unicodedata.name(ch, '')
    return not name.startswith('LATIN')


def has_non_ascii_letter(text):
    """
    Return True if `text` contains at least one non-Latin-script letter.

    Named for backward compatibility; despite the name it now excludes accented Latin letters,
    which are Latin script and should count as English.
    """
    if not text or not isinstance(text, str):
        return False
    return any(_is_non_latin_letter(ch) for ch in text)


def has_ascii_letter(text):
    """Return True if `text` contains at least one A-Z / a-z letter."""
    if not text or not isinstance(text, str):
        return False
    return any('a' <= ch.lower() <= 'z' for ch in text)


def is_non_english_text(text):
    """
    Return True when `text` should be treated as non-English (contains non-Latin script).

    Script-based and whitelist-free: a letter from any non-Latin script marks the text as
    non-English. Text that is empty, or contains only Latin letters (including accented Latin such
    as cafe/Jose), numbers, or symbols, is treated as English (nothing to translate). This replaces
    the previous per-file whitelist that silently skipped un-listed scripts, without introducing the
    accented-Latin false positive that a bare non-ASCII test would.
    """
    if not text or not isinstance(text, str):
        return False
    return has_non_ascii_letter(text)
