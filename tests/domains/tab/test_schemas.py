import pytest
from pydantic import ValidationError

from app.domains.tab.schemas import PlayingCondition, TabNote


def test_playing_condition_defaults_to_e_standard() -> None:
    condition = PlayingCondition()

    assert condition.tuning == ("E2", "A2", "D3", "G3", "B3", "E4")
    assert condition.strings == 6
    assert condition.capo == 0


def test_playing_condition_rejects_tuning_length_mismatch() -> None:
    with pytest.raises(ValidationError):
        PlayingCondition(tuning=("E2", "A2"), strings=6)


@pytest.mark.parametrize(
    "field, value",
    [("pitch", 128), ("confidence", 1.5), ("string", 0), ("fret", -1)],
)
def test_tab_note_rejects_out_of_range(field: str, value: float) -> None:
    fields = {
        "start": 0.0,
        "end": 0.1,
        "pitch": 64,
        "velocity": 0.5,
        "confidence": 0.9,
        "string": 1,
        "fret": 0,
        "beat": 0.0,
        "duration": 0.25,
    }

    with pytest.raises(ValidationError):
        TabNote(**{**fields, field: value})


def test_tab_note_round_trips_through_json() -> None:
    note = TabNote(
        start=0.5,
        end=0.7,
        pitch=64,
        velocity=0.5,
        confidence=0.8,
        bend=(0.0, 0.5),
        string=1,
        fret=0,
        beat=1.0,
        duration=0.5,
    )

    assert TabNote.model_validate_json(note.model_dump_json()) == note
    assert note.playable is True
