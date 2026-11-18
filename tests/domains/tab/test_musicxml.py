import xml.etree.ElementTree as ET

import pytest

from app.domains.tab.schemas import PlayingCondition, TabNote

pytest.importorskip("music21")

from app.domains.tab.pipeline.musicxml import build_musicxml  # noqa: E402

E_STANDARD = PlayingCondition()


def tab_note(pitch: int, string: int, fret: int, beat: float, duration: float = 1.0) -> TabNote:
    return TabNote(
        start=beat / 2,
        end=(beat + duration) / 2,
        pitch=pitch,
        velocity=0.5,
        confidence=0.9,
        beat=beat,
        duration=duration,
        string=string,
        fret=fret,
    )


def build(notes: list[TabNote], condition: PlayingCondition = E_STANDARD) -> ET.Element:
    data = build_musicxml(notes, condition, tempo_bpm=96.4)
    assert data.startswith(b"<?xml")
    return ET.fromstring(
        data.split(b"?>", 1)[1].split(b">", 1)[1] if b"<!DOCTYPE" in data else data
    )


def pitched(root: ET.Element) -> list[ET.Element]:
    return [n for n in root.iter("note") if n.find("pitch") is not None]


def string_fret(note: ET.Element) -> tuple[int, int]:
    return int(note.findtext("notations/technical/string")), int(
        note.findtext("notations/technical/fret")
    )


def test_single_notes_have_string_and_fret() -> None:
    root = build([tab_note(64, 1, 0, 0.0), tab_note(69, 1, 5, 1.0)])

    assert [string_fret(n) for n in pitched(root)] == [(1, 0), (1, 5)]


def test_every_chord_note_has_its_own_string_and_fret() -> None:
    chord = [tab_note(48, 5, 3, 0.0), tab_note(52, 4, 2, 0.0), tab_note(55, 3, 0, 0.0)]

    notes = pitched(build(chord))

    assert len(notes) == 3
    assert sum(n.find("chord") is not None for n in notes) == 2
    assert sorted(string_fret(n) for n in notes) == [(3, 0), (4, 2), (5, 3)]


def test_note_crossing_barline_is_tied_and_both_parts_have_fret() -> None:
    notes = pitched(build([tab_note(64, 2, 5, 3.0, 2.0)]))

    assert len(notes) == 2
    assert [string_fret(n) for n in notes] == [(2, 5), (2, 5)]
    assert notes[0].find("tie[@type='start']") is not None


def test_triplets_and_rests() -> None:
    triplet = [tab_note(60 + i, 2, 1 + i, 1 + i / 3, 1 / 3) for i in range(3)]

    root = build(triplet)

    assert len(pitched(root)) == 3
    assert all(n.find("time-modification") is not None for n in pitched(root))
    assert any(n.find("rest") is not None for n in root.iter("note"))


def test_staff_details_tuning_and_tab_clef() -> None:
    root = build([tab_note(64, 1, 0, 0.0)])

    details = root.find("part/measure/attributes/staff-details")
    assert details.findtext("staff-lines") == "6"
    tunings = [
        (t.get("line"), t.findtext("tuning-step"), t.findtext("tuning-octave"))
        for t in details.findall("staff-tuning")
    ]
    assert tunings[0] == ("1", "E", "2")
    assert tunings[-1] == ("6", "E", "4")
    assert root.findtext("part/measure/attributes/clef/sign") == "TAB"
    assert details.find("capo") is None


def test_capo_and_sharp_tuning() -> None:
    condition = PlayingCondition(tuning=("D#2", "G#2", "C#3", "F#3", "A#3", "D#4"), capo=2)

    details = build([tab_note(66, 1, 0, 0.0)], condition).find(
        "part/measure/attributes/staff-details"
    )

    assert details.findtext("capo") == "2"
    assert details.find("staff-tuning").findtext("tuning-alter") == "1"


def test_tempo_is_rounded() -> None:
    root = build([tab_note(64, 1, 0, 0.0)])

    assert root.findtext(".//metronome/per-minute") == "96"


def test_overlapping_notes_are_cut_at_next_onset() -> None:
    root = build([tab_note(40, 6, 0, 0.0, 4.0), tab_note(64, 1, 0, 1.0)])

    first, second = pitched(root)
    assert int(first.findtext("duration")) == int(second.findtext("duration"))


def test_music21_can_read_the_result_back() -> None:
    from music21 import converter

    data = build_musicxml(
        [tab_note(48, 5, 3, 0.0), tab_note(52, 4, 2, 0.0), tab_note(64, 1, 0, 1.0, 0.5)],
        E_STANDARD,
        tempo_bpm=120,
    )
    score = converter.parse(data, format="musicxml")

    assert len(list(score.recurse().notes)) == 2  # 화음 1개 + 단음 1개


def test_empty_notes_make_a_valid_score() -> None:
    root = build([])

    assert root.tag == "score-partwise"
    assert pitched(root) == []
