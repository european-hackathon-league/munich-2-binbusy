"""Deterministic, zero-latency policy compression.

The EHL policies (35k-65k characters) all share the same skeleton of
top-level ``PART <n> - <title>`` headers.  Instead of burning a ~20s
blocking LLM call on a policy digest to compress the wording before
pricing can start, we can simply slice out the parts that actually decide
Line Items. (That digest step, ``policy/digest.py``, is gone now -- this
slicer replaced it outright, at zero latency.)

Design constraints:

* No network, no LLM, no new dependencies, fully deterministic.
* The text inside a kept section is preserved **verbatim** (byte for byte),
  because downstream code checks that a model's quote is a verbatim
  substring of the policy.  Never reflow / normalise / strip whitespace
  inside a section.
* Section headers are kept so a quoted clause stays locatable.
* Fail *open*: if the structure is not recognised (e.g. Case 0's bicycle
  policy has no ``PART`` headers at all) or the slice comes out
  suspiciously small, return the full text rather than blinding the
  pricing engine.
"""

from __future__ import annotations

import re

# PART 3  EXCLUSIONS                     - what is not covered
# PART 4  INSURED PROPERTY / LOCATION    - what is covered at all + deductibles
# PART 5  INSURED COSTS                  - which costs are indemnifiable
# PART 7  CALCULATION OF THE INDEMNITY   - depreciation / betterment / scope
# PART 11 LOSS DESCRIPTION FOR THIS CLAIM - claim-specific answer key
DEFAULT_KEEP: frozenset[int] = frozenset({3, 4, 5, 7, 11})

#: Part number of the claim-specific section, present in only some cases.
CLAIM_SPECIFIC_PART = 11

#: If a slice comes out below this many characters we assume the parse went
#: wrong and fall back to the untouched policy text.
MIN_SLICE_CHARS = 2000

# ``PART 11 - LOSS DESCRIPTION ...``; the dash may be a hyphen, an en dash or
# an em dash.  Headers sit on their own line, optionally indented.
_HEADER_RE = re.compile(
    r"^[ \t]*PART[ \t]+(\d+)[ \t]*[\u2010-\u2015\-][ \t]*(.+?)[ \t]*$",
    re.MULTILINE,
)


#: Heads the paragraphs rescued from the dropped parts by `stated_amounts`.
AMOUNTS_HEADER = "AMOUNTS STATED IN OTHER PARTS OF THE POLICY"

_AMOUNT_RE = re.compile(r"\bEUR\s*\d|\d\s*EUR\b|\u20ac")
_PARAGRAPH_RE = re.compile(r"\n[ \t]*\n")


def stated_amounts(text: str) -> list[str]:
    """Every paragraph of *text* that states an amount of money, verbatim.

    The parts the slicer drops are mostly procedure, but a contract-specific figure can sit
    in any of them. Game 45's means-of-payment limit of EUR 500 stood only in PART 2
    ("of which means of payment are indemnified to no more than EUR 500"); PART 4 said no
    more than "the sub-limit stated in the schedule". Both readings therefore priced the
    stolen cash at 900. Eight of the first 47 Cases lose an amount this way.
    """
    return [
        paragraph.strip("\n")
        for paragraph in _PARAGRAPH_RE.split(text)
        if _AMOUNT_RE.search(paragraph)
    ]


#: Heads the clauses rescued from the dropped parts by `referenced_clauses`.
REFERENCED_HEADER = "CLAUSES THE LOSS DESCRIPTION REFERS TO IN OTHER PARTS OF THE POLICY"

_CLAUSE_NUMBER_RE = re.compile(r"(?<![\d.])\d+(?:\.\d+)+")
_CLAUSE_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)+)[ \t]+[A-Z]", re.MULTILINE)


def referenced_clauses(referring_text: str, dropped: dict[int, str]) -> list[str]:
    """Each clause of the *dropped* parts that *referring_text* names by number, verbatim.

    PART 11 lists "the operative provisions for determining how each line of each invoice
    in this claim is characterised", and some of them sit in parts the slicer drops. In the
    surge Games (1, 17, 19) that was 2.3.3: an appliance connected without an overvoltage
    protective device is paid at "one half of the amount otherwise payable". Both readings
    priced those appliances in full, about twice their Fair Value. In Games 31 and 45 it
    was 6.3: "the sub-limit determines the ceiling of the indemnity for that class even
    where the insurable value demonstrably exceeds it".

    A clause runs from its numbered heading to the next heading of the same part that is
    not one of its own sub-clauses. Only a number belonging to the part it stands in is a
    heading: a wrapped line that happens to begin with "2.3.3 and no other" in PART 9 is
    not. Clauses come back in document order, each once.
    """
    wanted = set(_CLAUSE_NUMBER_RE.findall(referring_text))
    found: list[str] = []
    for part in sorted(dropped):
        text = dropped[part]
        headings = [
            heading
            for heading in _CLAUSE_HEADING_RE.finditer(text)
            if heading.group(1).startswith(f"{part}.")
        ]
        covered_until = -1
        for position, heading in enumerate(headings):
            number = heading.group(1)
            if number not in wanted or heading.start() < covered_until:
                continue
            end = len(text)
            for later in headings[position + 1 :]:
                if not later.group(1).startswith(number + "."):
                    end = later.start()
                    break
            covered_until = end
            found.append(text[heading.start() : end].strip("\n"))
    return found


def _header_matches(policy_text: str) -> list[re.Match[str]]:
    return list(_HEADER_RE.finditer(policy_text))


def policy_sections(policy_text: str) -> dict[int, str]:
    """Split *policy_text* into ``{part number: verbatim section text}``.

    A section runs from the start of its ``PART n`` header line up to (but
    not including) the start of the next header line; the trailing chunk
    after the last header belongs to that last section.  Anything before
    the first header (title block / preamble) is not part of any section.

    Returns an empty dict when the document has no ``PART`` headers.  If a
    part number appears more than once, the last occurrence wins.
    """
    if not policy_text:
        return {}

    matches = _header_matches(policy_text)
    if not matches:
        return {}

    sections: dict[int, str] = {}
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(policy_text)
        sections[int(match.group(1))] = policy_text[start:end]
    return sections


def has_claim_specific_part(policy_text: str) -> bool:
    """True when the policy carries the claim-specific ``PART 11`` section."""
    return CLAIM_SPECIFIC_PART in policy_sections(policy_text)


def slice_policy(policy_text: str, keep: frozenset[int] = DEFAULT_KEEP) -> str:
    """Return only the pricing-relevant parts of *policy_text*.

    Falls back to the unchanged input when the policy has no recognisable
    ``PART`` headers, when none of the requested parts exist, or when the
    resulting slice looks implausibly short (< ``MIN_SLICE_CHARS``) while
    the original is longer than that.
    """
    if not policy_text:
        return policy_text

    sections = policy_sections(policy_text)
    if not sections:
        return policy_text

    kept = [sections[number] for number in sorted(sections) if number in keep]
    if not kept:
        return policy_text

    sliced = "\n".join(kept)
    dropped_parts = {number: text for number, text in sections.items() if number not in keep}
    dropped = "\n".join(dropped_parts[number] for number in sorted(dropped_parts))
    referenced = (
        referenced_clauses(sections[CLAIM_SPECIFIC_PART], dropped_parts)
        if CLAIM_SPECIFIC_PART in sections and CLAIM_SPECIFIC_PART in keep
        else []
    )
    if referenced:
        sliced = f"{sliced}\n{REFERENCED_HEADER}\n\n" + "\n\n".join(referenced) + "\n"
    # A paragraph already inside a rescued clause is not repeated.
    amounts = [
        paragraph
        for paragraph in stated_amounts(dropped)
        if not any(paragraph in clause for clause in referenced)
    ]
    if amounts:
        sliced = f"{sliced}\n{AMOUNTS_HEADER}\n\n" + "\n\n".join(amounts) + "\n"
    # A big policy that slices down to almost nothing means the parse went
    # wrong; hand back everything rather than blinding the pricing engine.
    if len(policy_text) >= MIN_SLICE_CHARS and len(sliced) < MIN_SLICE_CHARS:
        return policy_text
    return sliced


def slice_report(policy_text: str, keep: frozenset[int] = DEFAULT_KEEP) -> dict[str, object]:
    """Diagnostics for a single policy (used by tests and the CLI check)."""
    sections = policy_sections(policy_text)
    sliced = slice_policy(policy_text, keep)
    kept_parts = sorted(number for number in sections if number in keep)
    original_length = len(policy_text)
    return {
        "original_chars": original_length,
        "sliced_chars": len(sliced),
        "ratio": (len(sliced) / original_length) if original_length else 1.0,
        "all_parts": sorted(sections),
        "kept_parts": kept_parts,
        "has_part_11": CLAIM_SPECIFIC_PART in sections,
        "fell_back": sliced == policy_text,
    }


if __name__ == "__main__":  # pragma: no cover - manual verification helper
    from pathlib import Path

    cases_dir = Path(__file__).resolve().parents[3] / "[PUBLIC] EHL Cases" / "cases"
    total_original = 0
    total_sliced = 0
    print(f"{'case':>5} {'original':>9} {'sliced':>8} {'ratio':>7}  kept parts")
    for case_number in range(0, 15):
        policy_path = cases_dir / f"case_{case_number:02d}" / "policy.txt"
        if not policy_path.exists():
            continue
        text = policy_path.read_text(encoding="utf-8")
        report = slice_report(text)
        if report["all_parts"]:
            total_original += int(report["original_chars"])
            total_sliced += int(report["sliced_chars"])
        note = " (FALLBACK: no PART headers)" if not report["all_parts"] else ""
        print(
            f"{case_number:>5} {report['original_chars']:>9} {report['sliced_chars']:>8} "
            f"{report['ratio']:>6.1%}  {report['kept_parts']}{note}"
        )
    if total_original:
        print(f"\naggregate retention over structured policies: {total_sliced / total_original:.1%}")
