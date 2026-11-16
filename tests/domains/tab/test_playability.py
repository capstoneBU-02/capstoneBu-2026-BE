from app.domains.tab.pipeline.playability import check_playable
from app.domains.tab.schemas import TabNote


def make_note(string: int, fret: int, beat: float = 0.0, playable: bool = True) -> TabNote:
    return TabNote(
        start=beat,
        end=beat + 0.1,
        pitch=60,
        velocity=0.5,
        confidence=0.9,
        string=string,
        fret=fret,
        beat=beat,
        duration=0.25,
        playable=playable,
    )


def playable_flags(notes: list[TabNote]) -> list[bool]:
    return [note.playable for note in check_playable(notes)]


def test_empty_notes() -> None:
    assert check_playable([]) == []


def test_comfortable_chord_stays_playable() -> None:
    assert playable_flags([make_note(6, 3), make_note(5, 5), make_note(4, 5)]) == [True] * 3


def test_span_of_five_frets_is_allowed() -> None:
    assert playable_flags([make_note(6, 3), make_note(5, 8)]) == [True, True]


def test_span_over_five_frets_marks_pressed_notes() -> None:
    assert playable_flags([make_note(6, 3), make_note(5, 9)]) == [False, False]


def test_open_string_is_excluded_from_span() -> None:
    assert playable_flags([make_note(6, 0), make_note(5, 9)]) == [True, True]


def test_open_string_stays_playable_when_chord_is_too_wide() -> None:
    notes = [make_note(6, 0), make_note(5, 3), make_note(4, 9)]

    assert playable_flags(notes) == [True, False, False]


def test_move_of_seven_frets_within_sixteenth_marks_next_notes() -> None:
    notes = [make_note(1, 3, beat=0.0), make_note(1, 10, beat=0.25)]

    assert playable_flags(notes) == [True, False]


def test_move_under_seven_frets_is_allowed() -> None:
    notes = [make_note(1, 3, beat=0.0), make_note(1, 9, beat=0.25)]

    assert playable_flags(notes) == [True, True]


def test_large_move_is_allowed_with_more_time() -> None:
    notes = [make_note(1, 3, beat=0.0), make_note(1, 10, beat=0.5)]

    assert playable_flags(notes) == [True, True]


def test_open_string_between_does_not_hide_large_move() -> None:
    notes = [make_note(1, 3, beat=0.0), make_note(2, 0, beat=0.125), make_note(1, 10, beat=0.25)]

    assert playable_flags(notes) == [True, True, False]


def test_two_notes_on_same_string_are_marked() -> None:
    notes = [make_note(1, 3), make_note(1, 5), make_note(2, 3)]

    assert playable_flags(notes) == [False, False, True]


def test_same_string_at_different_beats_is_allowed() -> None:
    assert playable_flags([make_note(1, 3, beat=0.0), make_note(1, 5, beat=1.0)]) == [True, True]


def test_beats_within_float_error_are_one_group() -> None:
    assert playable_flags([make_note(1, 3, beat=0.1 + 0.2), make_note(1, 5, beat=0.3)]) == [
        False,
        False,
    ]


def test_input_order_is_kept_for_unsorted_notes() -> None:
    notes = [make_note(1, 10, beat=0.25), make_note(1, 3, beat=0.0)]

    result = check_playable(notes)

    assert [note.beat for note in result] == [0.25, 0.0]
    assert [note.playable for note in result] == [False, True]


def test_already_unplayable_note_stays_unplayable() -> None:
    assert playable_flags([make_note(1, 3, playable=False)]) == [False]


def test_input_notes_are_not_changed() -> None:
    notes = [make_note(1, 3), make_note(1, 5)]

    check_playable(notes)

    assert [note.playable for note in notes] == [True, True]
