"""Linear-time aggregate-title/count detection for provider-controlled text.

No numeric conversions, backtracking, or repeated suffix searches are used.
Each numeric run is consumed once. Only a fixed number of literal qualifiers
are tested at its end; whitespace runs can therefore be visited only a fixed
number of times. Title-wide lookahead is computed in one reverse pass rather
than scanning the remainder of the title for every number. Total work and
storage are O(n), with explicit caller-visible input budgets.
"""

MAX_TITLE_CHARS = 16_384
MAX_CONTENT_CHARS = 500_000

_NOUNS = ("job", "jobs", "vacancies", "openings", "roles")
_BROAD_NOUNS = ("job", "jobs", "openings", "vacancies")
_CONTENT_QUALIFIERS = (
    ("python",), ("backend",), ("frontend",), ("software",),
    ("developer",), ("engineer",), ("fresher",), ("entry", "level"),
)
_TITLE_QUALIFIERS = _CONTENT_QUALIFIERS + (("full", "stack"), ("internship",))
_OPENERS = ("hiring", "find", "search", "top", "browse", "all")


def _word(char: str) -> bool:
    # Python's Unicode regex \w semantics, without invoking a regex engine.
    return char == "_" or char.isalnum()


def _space_end(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _literal_end(text: str, pos: int, literals: tuple[str, ...]) -> int | None:
    for literal in literals:
        end = pos + len(literal)
        if text.startswith(literal, pos) and (end == len(text) or not _word(text[end])):
            return end
    return None


def _count_noun_end(text: str, pos: int, qualifiers: tuple[tuple[str, ...], ...]) -> int | None:
    pos = _space_end(text, pos)
    end = _literal_end(text, pos, _NOUNS)
    if end is not None:
        return end
    for qualifier in qualifiers:
        cursor = pos
        for part in qualifier:
            if not text.startswith(part, cursor):
                break
            cursor = _space_end(text, cursor + len(part))
        else:
            end = _literal_end(text, cursor, _NOUNS)
            if end is not None:
                return end
    return None


def _numeric_runs(text: str):
    pos = 0
    while pos < len(text):
        if not text[pos].isdecimal():
            pos += 1
            continue
        start = pos
        has_boundary = start == 0 or not _word(text[start - 1])
        pos += 1
        while pos < len(text) and (text[pos].isdecimal() or text[pos] in ",+"):
            # The original bounded grammar can begin after punctuation inside
            # a comma/plus run (for example ``foo1,000 jobs`` matches
            # ``000 jobs``). Track that possibility without rescanning the
            # run; the suffix is parsed only once by callers.
            if text[pos].isdecimal() and not _word(text[pos - 1]):
                has_boundary = True
            pos += 1
        yield start, pos, has_boundary


def has_content_aggregate_count(text: str) -> bool:
    """Match the old immediate count/optional-role/content noun rule in O(n)."""
    if len(text) > MAX_CONTENT_CHARS:
        return True  # Oversized content must not bypass scanning by truncation.
    lower = text.lower()
    return any(
        boundary and _count_noun_end(lower, end, _CONTENT_QUALIFIERS) is not None
        for _start, end, boundary in _numeric_runs(lower)
    )


def _title_count(text: str) -> bool:
    # The legacy broad rule allows arbitrary same-line text after a count and
    # whitespace, even e.g. "24 Python Fresher Job Vacancies". Compute whether
    # a target is later on this line once, not once for each candidate number.
    later_noun = bytearray(len(text) + 1)
    seen = False
    for pos in range(len(text) - 1, -1, -1):
        if text[pos] == "\n":
            seen = False
        elif _literal_end(text, pos, _BROAD_NOUNS) is not None:
            seen = True
        later_noun[pos] = seen

    for start, end, boundary in _numeric_runs(text):
        if boundary:
            if _count_noun_end(text, end, _TITLE_QUALIFIERS) is not None:
                return True
            if end < len(text) and text[end].isspace() and later_noun[_space_end(text, end)]:
                return True

        # Parenthesized counts also permit "(1,000+ Open Roles)".
        before = start - 1
        while before >= 0 and text[before].isspace():
            before -= 1
        if before >= 0 and text[before] == "(":
            cursor = _space_end(text, end)
            if text.startswith("open", cursor):
                after_open = cursor + 4
                noun_end = _literal_end(text, _space_end(text, after_open), ("roles",))
                if noun_end is not None:
                    close = _space_end(text, noun_end)
                    if close < len(text) and text[close] == ")":
                        return True
    return False


def _location_suffix(text: str) -> tuple[int, int]:
    # "jobs in <word/space/comma location> [ - YYYY]" must end the title.
    # Locate the eligible suffix once to avoid rescanning it per "jobs in".
    end = len(text)
    if end >= 4 and all(char.isdecimal() for char in text[-4:]):
        pos = end - 5
        while pos >= 0 and text[pos].isspace():
            pos -= 1
        if pos >= 0 and text[pos] == "-":
            end = pos
    start = end
    while start > 0 and (_word(text[start - 1]) or text[start - 1].isspace() or text[start - 1] == ","):
        start -= 1
    return start, end


def _title_phrase(text: str) -> bool:
    suffix_start, suffix_end = _location_suffix(text)
    opener_end = None
    opener_space_end = None
    anchored_hiring_end = None
    if text.startswith("hiring") and len(text) > 6 and text[6].isspace():
        anchored_hiring_end = _space_end(text, 6)

    for pos in range(len(text)):
        # The arbitrary phrase portion cannot cross a newline, but the
        # whitespace immediately after its opener may include newlines.
        if text[pos] == "\n":
            if opener_space_end is not None and pos >= opener_space_end:
                opener_end = opener_space_end = None
            if anchored_hiring_end is not None and pos >= anchored_hiring_end:
                anchored_hiring_end = None

        boundary = pos == 0 or not _word(text[pos - 1])
        if boundary and opener_end is None:
            end = _literal_end(text, pos, _OPENERS)
            if end is not None and end < len(text) and text[end].isspace():
                opener_end = end
                opener_space_end = _space_end(text, end)

        if text.startswith("jobs", pos):
            end = pos + 4
            if end < len(text) and text[end].isspace():
                in_pos = _space_end(text, end)
                in_end = _literal_end(text, in_pos, ("in",))
                if in_end is not None:
                    if anchored_hiring_end is not None and pos >= anchored_hiring_end:
                        return True
                    if (opener_end is not None and pos >= opener_end + 2
                            and text[pos - 1].isspace()):
                        return True
                    if boundary and in_end < len(text) and text[in_end].isspace():
                        # Consume one required separator; remaining spaces
                        # may themselves be part of the legacy location rule.
                        location = in_end + 1
                        if suffix_start <= location < suffix_end:
                            return True

        if boundary and text.startswith("job", pos):
            end = pos + 3
            if end < len(text) and text[end].isspace():
                vacancies = _space_end(text, end)
                end = vacancies + len("vacancies")
                if text.startswith("vacancies", vacancies) and end < len(text) and text[end].isspace():
                    if _literal_end(text, _space_end(text, end), ("in",)) is not None:
                        return True
    return False


def title_aggregate_reason(title: str | None) -> str | None:
    """Return the existing count/phrase reason, or fail closed over budget."""
    if not isinstance(title, str) or not title:
        return None
    if len(title) > MAX_TITLE_CHARS:
        return "title_too_long"
    clean = title.strip().lower()
    if _title_count(clean):
        return "title_aggregate_count"
    if _title_phrase(clean):
        return "title_aggregate_phrase"
    return None
