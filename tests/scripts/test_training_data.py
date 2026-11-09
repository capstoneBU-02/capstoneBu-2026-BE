import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def load_module() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts" / "train" / "training_data.py"
    spec = importlib.util.spec_from_file_location("training_data", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


data = load_module()
HEADER = "track_id,song,split,kind,audio,midi\n"
VALID_ROWS = (
    "a,song1,train,electric,a.wav,a.mid\n"
    "b,song2,validation,electric,b.wav,b.mid\n"
    "c,song3,test,acoustic,c.wav,c.mid\n"
)


def write_manifest(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "manifest.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_read_manifest_resolves_paths_from_manifest_folder(tmp_path: Path) -> None:
    tracks = data.read_manifest(write_manifest(tmp_path, HEADER + VALID_ROWS))

    assert [track.split for track in tracks] == ["train", "validation", "test"]
    assert tracks[0].audio == tmp_path / "a.wav"


def test_read_manifest_accepts_excel_bom(tmp_path: Path) -> None:
    tracks = data.read_manifest(write_manifest(tmp_path, "﻿" + HEADER + VALID_ROWS))

    assert len(tracks) == 3


def test_read_manifest_rejects_missing_column(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="midi"):
        data.read_manifest(write_manifest(tmp_path, "track_id,song,split,kind,audio\n"))


def test_read_manifest_rejects_song_in_two_splits(tmp_path: Path) -> None:
    rows = VALID_ROWS + "d,song1,test,electric,d.wav,d.mid\n"

    with pytest.raises(ValueError, match="song1"):
        data.read_manifest(write_manifest(tmp_path, HEADER + rows))


def test_read_manifest_rejects_duplicate_track_id(tmp_path: Path) -> None:
    rows = VALID_ROWS + "a,song4,train,electric,d.wav,d.mid\n"

    with pytest.raises(ValueError, match="중복"):
        data.read_manifest(write_manifest(tmp_path, HEADER + rows))


@pytest.mark.parametrize(
    "row, message",
    [
        ("d,song4,dev,electric,d.wav,d.mid\n", "split"),
        ("d,song4,train,bass,d.wav,d.mid\n", "kind"),
        ("한글,song4,train,electric,d.wav,d.mid\n", "track_id"),
    ],
)
def test_read_manifest_rejects_invalid_values(tmp_path: Path, row: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        data.read_manifest(write_manifest(tmp_path, HEADER + VALID_ROWS + row))


def test_read_manifest_requires_every_split(tmp_path: Path) -> None:
    rows = "a,song1,train,electric,a.wav,a.mid\n"

    with pytest.raises(ValueError, match="validation"):
        data.read_manifest(write_manifest(tmp_path, HEADER + rows))


def test_build_labels_marks_note_onset_and_contour_center() -> None:
    fps = data.ANNOTATIONS_FPS
    labels = data.build_labels([data.NoteEvent(1.0, 1.0 + 3 / fps, 64)], duration=2.0)

    semitone = 64 - 21
    assert labels.onsets == [(fps, semitone)]
    assert labels.notes == [(fps + i, semitone) for i in range(3)]
    assert labels.contours == [(fps + i, semitone * 3 + 1) for i in range(3)]
    assert labels.n_frames == 2 * fps + 1
    assert labels.skipped_notes == 0


def test_build_labels_keeps_very_short_note_for_one_frame() -> None:
    labels = data.build_labels([data.NoteEvent(1.0, 1.0, 64)], duration=2.0)

    assert len(labels.notes) == 1


def test_build_labels_skips_notes_out_of_range() -> None:
    notes = [
        data.NoteEvent(0.0, 0.1, 20),
        data.NoteEvent(0.0, 0.1, 109),
        data.NoteEvent(5.0, 5.1, 64),
    ]

    labels = data.build_labels(notes, duration=2.0)

    assert labels.notes == []
    assert labels.skipped_notes == 3


def test_build_labels_clips_note_at_audio_end() -> None:
    labels = data.build_labels([data.NoteEvent(1.9, 5.0, 64)], duration=2.0)

    assert max(frame for frame, _ in labels.notes) == labels.n_frames - 1


def test_build_labels_merges_overlapping_same_pitch() -> None:
    notes = [data.NoteEvent(1.0, 1.1, 64), data.NoteEvent(1.0, 1.1, 64)]

    labels = data.build_labels(notes, duration=2.0)

    assert len(labels.onsets) == 1
    assert labels.notes == sorted(set(labels.notes))
