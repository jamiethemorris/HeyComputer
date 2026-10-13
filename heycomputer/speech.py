"""Literal, user-defined pronunciation corrections; no model roundtrip."""
import re

from .protocol import PlanError


def correction_pairs(value):
    if not isinstance(value, str) or len(value) > 8000:
        raise PlanError("Speech corrections must be under 8,000 characters.")
    pairs, seen = [], set()
    for line in value.splitlines():
        if not line.strip():
            continue
        parts = re.split(r"\s*=>?\s*", line, maxsplit=1)
        if len(parts) != 2 or not all(0 < len(p.strip()) <= 120 for p in parts):
            raise PlanError("Use one speech correction per line: heard phrase = correct spelling.")
        heard, correct = (p.strip() for p in parts)
        key = " ".join(heard.casefold().split())
        if key in seen:
            raise PlanError("Each heard phrase can have only one speech correction.")
        seen.add(key)
        pairs.append((heard, correct))
    if len(pairs) > 128:
        raise PlanError("Use at most 128 speech corrections.")
    return pairs


def correct_text(text, config):
    pairs = sorted(correction_pairs(config.get("speech_corrections", "")), key=lambda p: -len(p[0]))
    if not pairs:
        return text
    patterns = [r"(?P<c%d>(?<!\w)%s(?!\w))" % (i, r"\s+".join(re.escape(w) for w in heard.split()))
                for i, (heard, _) in enumerate(pairs)]
    # One pass prevents replacement text from being interpreted as another alias.
    return re.sub("|".join(patterns), lambda m: pairs[int(m.lastgroup[1:])][1], text, flags=re.I)
