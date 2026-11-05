from collections.abc import Sequence

import pytest

from app.core.config import Settings
from app.domains.tab.pipeline import fingering
from app.domains.tab.pipeline.fingering import assign_fingering, pitch_from_name
from app.domains.tab.pipeline.playability import check_playable
from app.domains.tab.schemas import PlayingCondition, QuantizedNote, TabNote

E_STANDARD = PlayingCondition()


@pytest.fixture(autouse=True)
def default_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """로컬 .env와 상관없이 기본 가중치로 검사한다."""
    monkeypatch.setattr(fingering, "get_settings", lambda: Settings(_env_file=None))


def make_note(pitch: int, beat: float = 0.0) -> QuantizedNote:
    return QuantizedNote(
        start=beat,
        end=beat + 0.1,
        pitch=pitch,
        velocity=0.5,
        confidence=0.9,
        beat=beat,
        duration=0.25,
    )


def chord(pitches: Sequence[int], beat: float = 0.0) -> list[QuantizedNote]:
    return [make_note(pitch, beat) for pitch in pitches]


def positions(notes: Sequence[TabNote]) -> list[tuple[int, int]]:
    return [(note.string, note.fret) for note in notes]


@pytest.mark.parametrize(
    "name, pitch",
    [("E2", 40), ("A2", 45), ("C4", 60), ("E4", 64), ("F#3", 54), ("Bb2", 46)],
)
def test_pitch_from_name(name: str, pitch: int) -> None:
    assert pitch_from_name(name) == pitch


@pytest.mark.parametrize("name", ["", "H2", "E", "e2"])
def test_pitch_from_name_rejects_invalid_name(name: str) -> None:
    with pytest.raises(ValueError):
        pitch_from_name(name)


def test_empty_notes() -> None:
    assert assign_fingering([], E_STANDARD) == []


@pytest.mark.parametrize(
    "pitch, expected",
    [(40, (6, 0)), (45, (5, 0)), (50, (4, 0)), (55, (3, 0)), (59, (2, 0)), (64, (1, 0))],
)
def test_single_note_prefers_open_string(pitch: int, expected: tuple[int, int]) -> None:
    assert positions(assign_fingering([make_note(pitch)], E_STANDARD)) == [expected]


def test_note_keeps_its_fields() -> None:
    note = make_note(64, beat=1.5)

    result = assign_fingering([note], E_STANDARD)[0]

    assert result.model_dump(include=set(QuantizedNote.model_fields)) == note.model_dump()
    assert result.playable is True


def test_every_position_sounds_the_same_pitch() -> None:
    open_pitches = {6: 40, 5: 45, 4: 50, 3: 55, 2: 59, 1: 64}
    notes = [make_note(pitch, beat=i * 0.5) for i, pitch in enumerate(range(40, 87))]

    for note in assign_fingering(notes, E_STANDARD):
        assert open_pitches[note.string] + note.fret == note.pitch
        assert note.playable is True


def test_next_note_stays_near_the_hand() -> None:
    notes = [make_note(76, beat=0.0), make_note(65, beat=0.25)]

    assert positions(assign_fingering(notes, E_STANDARD)) == [(1, 12), (3, 10)]


def test_open_chord_uses_open_position() -> None:
    c_major = chord([48, 52, 55, 60, 64])

    result = assign_fingering(c_major, E_STANDARD)

    assert positions(result) == [(5, 3), (4, 2), (3, 0), (2, 1), (1, 0)]
    assert all(note.playable for note in check_playable(result))


def test_six_open_strings_use_every_string() -> None:
    result = assign_fingering(chord([40, 45, 50, 55, 59, 64]), E_STANDARD)

    assert positions(result) == [(6, 0), (5, 0), (4, 0), (3, 0), (2, 0), (1, 0)]


def test_wide_chord_avoids_span_over_limit() -> None:
    # 낮은 프렛으로만 고르면 범위가 넓어지는 화음. 줄을 옮겨 4프렛 안으로 잡아야 한다
    result = assign_fingering(chord([43, 53, 62]), E_STANDARD)

    frets = [note.fret for note in result if note.fret > 0]
    assert max(frets) - min(frets) <= 4


def test_notes_with_only_one_string_share_it_and_are_flagged() -> None:
    result = assign_fingering(chord([40, 41]), E_STANDARD)

    assert positions(result) == [(6, 0), (6, 1)]
    assert [note.playable for note in check_playable(result)] == [False, False]


def test_more_notes_than_strings_keeps_every_note() -> None:
    pitches = [40, 45, 50, 55, 59, 64, 69]

    result = assign_fingering(chord(pitches), E_STANDARD)

    assert [note.pitch for note in result] == pitches
    assert not all(note.playable for note in check_playable(result))


def test_pitch_above_max_fret_goes_to_nearest_position() -> None:
    result = assign_fingering([make_note(88)], E_STANDARD)[0]

    assert (result.string, result.fret) == (1, 22)
    assert result.playable is False


def test_pitch_below_lowest_string_goes_to_nearest_position() -> None:
    result = assign_fingering([make_note(30)], E_STANDARD)[0]

    assert (result.string, result.fret) == (6, 0)
    assert result.playable is False


def test_input_order_is_kept_for_unsorted_notes() -> None:
    notes = [make_note(64, beat=1.0), make_note(40, beat=0.0)]

    assert positions(assign_fingering(notes, E_STANDARD)) == [(1, 0), (6, 0)]


def test_capo_raises_open_strings() -> None:
    condition = PlayingCondition(capo=2)

    assert positions(assign_fingering([make_note(66)], condition)) == [(1, 0)]


def test_other_tuning_and_string_count() -> None:
    bass = PlayingCondition(tuning=("E1", "A1", "D2", "G2"), strings=4)

    assert positions(assign_fingering([make_note(28), make_note(43, 1.0)], bass)) == [
        (4, 0),
        (1, 0),
    ]


def test_max_states_limits_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fingering,
        "get_settings",
        lambda: Settings(_env_file=None, ai_tab_max_states=1),
    )

    result = assign_fingering(chord([48, 52, 55, 60, 64]), E_STANDARD)

    assert positions(result) == [(5, 3), (4, 2), (3, 0), (2, 1), (1, 0)]
