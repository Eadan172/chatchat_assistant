"""Log-only confidence notes.

There is no built-in production cutoff. A floor is used only when the operator
stored one. These helpers describe that fact; they do not change chat.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConfidenceNote:
    confidence: float
    apply_floor: float | None
    band: str
    note: str

    def as_dict(self) -> dict[str, object]:
        return {
            "confidence": self.confidence,
            "apply_floor": self.apply_floor,
            "band": self.band,
            "note": self.note,
        }


def describe_confidence(confidence: float, *, apply_floor: float | None) -> ConfidenceNote:
    """Describe confidence against an operator floor, if one was configured."""
    number = round(float(confidence), 4)
    if apply_floor is None:
        return ConfidenceNote(
            confidence=number,
            apply_floor=None,
            band="unconfigured",
            note="no production confidence floor is configured",
        )
    floor = round(float(apply_floor), 4)
    if number >= floor:
        return ConfidenceNote(
            confidence=number,
            apply_floor=floor,
            band="at_or_above_floor",
            note="confidence meets the configured floor",
        )
    return ConfidenceNote(
        confidence=number,
        apply_floor=floor,
        band="below_floor",
        note="confidence is below the configured floor",
    )


def eligible_to_apply(
    note: ConfidenceNote,
    *,
    shadow: bool,
    real_routing: bool,
    decider_ok: bool,
) -> bool:
    """Whether a successful probe may be marked applied.

    Shadow mode and a disabled routing flag never apply. When routing is on
    and no floor is configured, the probe is eligible: this file does not
    invent a cutoff. A configured floor excludes confidence below it.
    """
    if shadow or not real_routing or not decider_ok:
        return False
    if note.apply_floor is None:
        return True
    return note.band == "at_or_above_floor"
