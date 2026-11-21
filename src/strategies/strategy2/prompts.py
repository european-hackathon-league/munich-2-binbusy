"""What we ask the model, and what has already been tried and failed.

The model returns **evidence only** — a coverage probability, a gross-total price band and
a quoted Policy clause. It never returns a Charge, a Limit or a Fair Value; `src/pricing/engine.py`
derives those deterministically (ADR 0001). Two regenerations over one invoice must not
disagree about the number we are scored on.
"""

from __future__ import annotations

from src.strategies.strategy2.constants import SETTLED_MEDIAN

#: Every figure here is measured over the 457 settled Line Items whose Fair Value we have
#: recovered, and every one of them used to be too low. The hint previously said the median
#: was 59 (it is 97), that a quarter fall under 20 (it is 25), and that the top decile "runs
#: past 400 EUR to several thousand" -- when p90 is 616, p99 is 2,345 and the largest settled
#: position we have seen is 11,131. Understating a distribution to a model is not a neutral
#: act: it anchors low, and it anchors hardest exactly where we are already measurably worst,
#: which is the expensive tail. On Game 41 a watch declared on a valuables schedule settled
#: at `t >= 11,131` and we priced it at 5,524.
_DISTRIBUTION_HINT = (
    "\nFor reference, here is the settled distribution of these positions, measured over 457 "
    "positions whose true value is known. It is wide and very skewed:\n"
    "  25% under 25 EUR   median "
    f"{SETTLED_MEDIAN:.0f} EUR"
    "   75% under 330 EUR   90% under 616 EUR   99% under 2,345 EUR\n"
    "The remaining 1% runs into five figures; the largest settled position seen so far is "
    "11,131 EUR. Expensive positions are rarer than they look, and they are typically "
    "specialist restoration or a whole-system replacement rather than a labour line.\n"
    "Use this as a sanity check on the shape, never as an anchor for an individual position. "
    "Pricing an expensive item like the median is an expensive mistake, and so is pricing an "
    "ordinary one like an expensive one: above the Fair Value we collect nothing.\n"
)

_TEMPLATE = """Read this insurance Case and return evidence for every invoice Line Item.

Do not return a Charge, an Acceptance Limit, or a Fair Value. Deterministic code prices your evidence.

For each Line Item return:
- line_item: the POS number printed on the invoice. Use it exactly. Numbering may skip a number and may continue across several invoices in the same document.
- coverage_probability: the probability from 0 to 1 that this Policy indemnifies this position at all. This is the most valuable number you produce. Nearly half of positions are worth nothing.
- price_low, price_median, price_high: a realistic GROSS TOTAL band in EUR for the WHOLE Line Item at German market prices. Never a net amount, never a per-unit price. Make the band honest: wide when you are unsure, narrow when you are confident.
- ceiling: the most this Policy can pay for this Line Item, in EUR gross, when the Policy states an amount for its class of property or its head of cost (a sub-limit, a per-item or per-article limit, "indemnity is capped at"). Use the figure this contract states. Add 19% when it is stated exclusive of value added tax. When one Line Item bundles several classes (a watch and cash in one line), add their ceilings. null when the Policy states no amount.
- clause: the section number and the first ten words of the Policy sentence that decides coverage. Nothing more.

Price the actual work at real German market rates, and get the LEVEL right. Both directions cost us money and neither is safe:
- Too low: we forfeit the difference from every single opponent, because a fair Charge is owed whether or not it is accepted.
- Too high: we collect nothing at all.

Anchors, since gross totals for a whole Line Item are easy to get wrong by an order of magnitude:
- Tradesman labour runs roughly 60-110 EUR per hour, so an hourly Line Item is that rate multiplied by the hours: 6.75 technician hours is several hundred EUR, not tens.
- Small parts, fittings, screws and consumables are genuinely cheap: tens of EUR for the whole position.
- Leak detection, leak pinpointing and moisture surveys settle around 430 EUR and reach 850. They are NOT a small call-out fee: seven in ten are above 400.
- Drying (room, cavity, insulation-layer) settles around 425 EUR; large-area or borehole drying reaches 1,400-2,600.
- Damage assessment, inspection and surveys settle around 490 EUR and reach 920.
- Disposal, strip-out and removal are cheaper, around 130 EUR, but a full strip-out reaches 1,000+.
- Equipment and machinery hire runs 50-700 EUR per position.
- Appliances, electronics, restoration and structural work reach the low thousands.
- A thermal imaging camera billed as its own position is a small equipment charge of 15-20 EUR, not a leak-detection service.

{distribution}
Amounts the Policy states. Find them first, because they override every market price:
- List every EUR amount in the Policy: class sub-limits (valuables, means of payment, bicycles and mobility equipment, appliances, unspecified high-value articles), caps on a head of cost, and the deductible.
- A Line Item is worth the LOWER of its market price and the ceiling of its class. A stolen watch bought for 7,000 under a EUR 2,500 valuables sub-limit is worth 2,500: not 7,000, and not the price of the replacement. 900 in cash under a EUR 500 means-of-payment limit is worth 500.
- The ceiling applies "whatever the actual value" unless the Policy text itself records an agreed value or a raised sub-limit for that item. A purchase price, a receipt or a valuation in the Damage Description does not lift it.
- When the loss is clearly above the ceiling, the ceiling is the value: put price_median at the ceiling and keep the band narrow.
- A ceiling "in the aggregate per insured event" is shared by every Line Item of that class. Their price_median values together must not exceed it.
- A deductible is taken off once per insured event, shared across the covered Line Items in proportion to their value. With ceilings of 2,500 and 500 and a deductible of 150, the two lines are worth 2,375 and 475.
- A ceiling is a limit on a covered line, never a reason to call it uncovered. A line that compensates stolen or destroyed insured property is covered up to its ceiling.

Upgrades. The Policy pays for what was there before, never for the improvement:
- A position that bills the WHOLE replacement in a better version (a larger air conditioning unit, chrome instead of plain copper, a higher-spec watch or television) is covered. Price the OLD item in standard grade: the 3.5 kW unit that failed, not the 7.1 kW unit on the invoice. Do not lower coverage_probability because it is an upgrade: all seven such positions in the settled Games were paid.
- A position that bills ONLY the improvement (an upgrade surcharge, a "difference", an elective better material or model on its own line) is worth nothing. None of the four in the settled Games had a Charge proven fair.
- Something that was not there before the event at all (an added sensor, an extra valve) is worth nothing.

Age and wear. Whether an old item is paid at its new price is decided by the Policy, not by common sense:
- Find how this Policy values the item's class. "Replacement price in new condition" or "necessary repair costs" means no deduction, however old the item is: a four-year-old television still settled in the hundreds.
- "Current value" or "less a deduction for age, wear and remaining useful life" means the item is worth clearly less than its new price. Use the age, purchase date and condition given in the Damage Description to size the deduction.
- An agreed new-for-old option removes the deduction: a seven-year-old table whose model still costs EUR 440 net settled at 524.
- A clause can take an aged item out of cover entirely (a unit whose battery has fallen below 80% of its design capacity, unless a battery health waiver is agreed). Then every position on that item is worth nothing.

How to judge coverage:
- Judge the SERVICE BEING BILLED, not the object it concerns. Inspection, leak detection, drying and assessment are frequently indemnified even when the item investigated is not insured.
- Read cross-references to the end. An exclusion that finishes with wording like "the head of cost under 5.2.6 remains unaffected" is a pointer to cover, not an exclusion.
- Read the WHOLE clause, because the half that narrows cover is usually followed by the half that restores it. Two specific cases, both of which have cost us real money:
  - SCOPE. A clause confining indemnity to "those parts that were themselves affected" is normally followed by an extension: where a room was wetted as a whole, the whole of that room counts as affected for extraction, drying and the reinstatement of its finishes. A Damage Description saying the water reached "about a square metre" does NOT narrow that extension. Price the quantity the invoice bills — 18 m2 of parquet, 15 m of skirting, a whole ceiling — not the square metre the description mentions.
  - CONSUMABLES. The exclusion of "material whose type, quantity or dimension does not correspond to the installation" ends by sending minor fixings and consumables back to the covered materials head. Clamps, binding wire, screws and sealing material used on the covered repair stay covered.
  - PERIL. An exclusion printed inside one peril's own section removes cover only for that peril, not for the Policy. Flood, precipitation, groundwater and backing-up are excluded from the escape-of-water peril and are separately insurable as natural hazards. If no Schedule is supplied, do not read a peril-scoped exclusion as though it excluded the whole claim.
- The Damage Description is the statement of facts, and it is where most worthless positions are named. When it says an item was not affected by the event, was lost or damaged separately, was already there or already damaged before, was added new, or was the owner's own choice, apply the Policy clause to that fact: the position is not covered. The same holds when the Line Item name carries such a remark itself.
- A position that installs, delivers, connects or disposes of an item that is itself not covered is not covered either.
- A fact that no Policy clause attaches a consequence to changes nothing. Only a Policy clause excludes.
- None of this makes an excluded item covered. Working equipment the contractor takes away again, an item claimed with no proof of ownership, the policyholder's own time and travel, cleaning and general upkeep, and a second travel charge on one invoice all stay at ~0. The point is narrower: do not let a narrow reading of the damage shrink a scope the Policy grants, and do not let one peril's exclusion list decide a claim brought under another peril.
- quantity_missing=true means the invoice printed no amount and no unit, only dashes. Every such position in the settled Cases was worth exactly 0.
- An implausible quantity means the position is priced for the plausible quantity, not that it is excluded.

Return JSON only:
{{"items":[{{"line_item":1,"coverage_probability":0.9,"price_low":0.0,"price_median":0.0,"price_high":0.0,"ceiling":null,"clause":""}}]}}"""

#: The anchored framing: the model is told the settled distribution as a shape check.
PROMPT = _TEMPLATE.format(distribution=_DISTRIBUTION_HINT)

#: The same question with the distribution paragraph deleted. Deleting it measurably moves
#: the level -- on Games 1-15 and 17-19 the median `t_hat / t` on Line Items worth 400-1000
#: EUR goes from 0.93 to 1.10 and the share we under-price from 62% to 48% -- but on its own
#: it is *not* worth money: replayed against the real Field it lands at +14,260 against the
#: anchored prompt's +54,230, because the same shift also pushes items above `t`, where
#: income is zero. It earns its place only as the second ensemble member: the two framings
#: are wrong in different directions, and their disagreement is the only honest width signal
#: we have.
PROMPT_UNANCHORED = _TEMPLATE.format(distribution="")

#: One call per framing, fired concurrently, so the wall clock is the slower draw rather
#: than the sum. Two is the measured sweet spot: +28,625 over a single call across Games
#: 1-15 and 17-19. More members keep improving the median Game but each is another chance
#: to miss the 60 s window.
ENSEMBLE_PROMPTS = (PROMPT, PROMPT_UNANCHORED)

__all__ = ["ENSEMBLE_PROMPTS", "PROMPT", "PROMPT_UNANCHORED"]
