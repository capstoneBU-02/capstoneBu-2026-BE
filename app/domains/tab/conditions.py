"""지원 연주 조건. PoC는 E 스탠다드 6현, 카포 0, 원곡 재현형만 지원한다 (D09, TAB-01·02)."""

from dataclasses import dataclass

from app.domains.tab.schemas import PlayingCondition

DEFAULT_RESULT_TYPE = "original"
DEFAULT_TUNING_ID = "e_standard"
DEFAULT_CAPO = 0


@dataclass(frozen=True)
class Tuning:
    id: str
    name: str
    notes: tuple[str, ...]  # 저음 줄부터


TUNINGS = {
    "e_standard": Tuning("e_standard", "E 스탠다드", ("E2", "A2", "D3", "G3", "B3", "E4")),
}
RESULT_TYPES = {"original": "원곡 재현형"}
CAPO_RANGE = (0, 0)


def is_supported(result_type: str, tuning_id: str, capo: int) -> bool:
    return (
        result_type in RESULT_TYPES
        and tuning_id in TUNINGS
        and CAPO_RANGE[0] <= capo <= CAPO_RANGE[1]
    )


def playing_condition(tuning_id: str, capo: int) -> PlayingCondition:
    """운지·MusicXML 단계에 넘기는 연주 조건."""
    notes = TUNINGS[tuning_id].notes
    return PlayingCondition(tuning=notes, strings=len(notes), capo=capo)
