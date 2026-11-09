"""학습 데이터 목록(manifest)과 정답 라벨. TensorFlow 없이 동작해 CI에서 테스트한다.

manifest.csv 열: track_id, song, split(train·validation·test), kind(electric·acoustic), audio, midi
경로는 manifest 파일 기준 상대 경로다. 같은 곡이 여러 split에 섞이면 성능이 부풀려지므로 막는다.
"""

import csv
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

# basic_pitch.constants와 같은 값. prepare_data.py가 실행할 때 일치 여부를 확인한다
AUDIO_SAMPLE_RATE = 22050
ANNOTATIONS_FPS = AUDIO_SAMPLE_RATE // 256
LOWEST_MIDI_PITCH = 21  # 27.5Hz (A0)
N_FREQ_BINS_NOTES = 88
CONTOURS_BINS_PER_SEMITONE = 3
N_FREQ_BINS_CONTOURS = N_FREQ_BINS_NOTES * CONTOURS_BINS_PER_SEMITONE

SPLITS = ("train", "validation", "test")
KINDS = ("electric", "acoustic")
REQUIRED_COLUMNS = ("track_id", "song", "split", "kind", "audio", "midi")
TRACK_ID = re.compile(r"[A-Za-z0-9._-]+")


@dataclass(frozen=True)
class Track:
    track_id: str
    song: str
    split: str
    kind: str
    audio: Path
    midi: Path


@dataclass(frozen=True)
class NoteEvent:
    start: float  # 초
    end: float
    pitch: int  # MIDI 번호


@dataclass(frozen=True)
class Labels:
    """희소 행렬 형태의 정답. 인덱스는 (프레임, 주파수 칸)이다."""

    notes: list[tuple[int, int]]
    onsets: list[tuple[int, int]]
    contours: list[tuple[int, int]]
    n_frames: int
    skipped_notes: int


def read_manifest(path: Path) -> list[Track]:
    """manifest.csv를 읽고 검사한다. 문제가 있으면 전부 모아 ValueError로 알린다."""
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        missing = set(REQUIRED_COLUMNS) - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"manifest에 없는 열: {sorted(missing)}")
        tracks = [_to_track(row, path.parent) for row in reader]

    errors = _find_errors(tracks)
    if errors:
        raise ValueError("manifest 오류:\n- " + "\n- ".join(errors))
    return tracks


def _to_track(row: dict[str, str], base: Path) -> Track:
    return Track(
        track_id=row["track_id"].strip(),
        song=row["song"].strip(),
        split=row["split"].strip(),
        kind=row["kind"].strip(),
        audio=base / row["audio"].strip(),
        midi=base / row["midi"].strip(),
    )


def _find_errors(tracks: list[Track]) -> list[str]:
    errors = []
    seen: set[str] = set()
    song_splits: defaultdict[str, set[str]] = defaultdict(set)
    for track in tracks:
        if not TRACK_ID.fullmatch(track.track_id):
            errors.append(f"{track.track_id!r}: track_id는 영문, 숫자, . _ - 만 쓸 수 있습니다")
        if track.track_id in seen:
            errors.append(f"{track.track_id}: track_id가 중복됩니다")
        seen.add(track.track_id)
        if track.split not in SPLITS:
            errors.append(f"{track.track_id}: split은 {SPLITS} 중 하나여야 합니다")
        if track.kind not in KINDS:
            errors.append(f"{track.track_id}: kind는 {KINDS} 중 하나여야 합니다")
        song_splits[track.song].add(track.split)

    for song, splits in song_splits.items():
        if len(splits) > 1:
            errors.append(f"곡 {song}: 여러 split에 섞여 있습니다 ({sorted(splits)})")

    used = {track.split for track in tracks}
    for split in SPLITS:
        if split not in used:
            errors.append(f"{split} split에 곡이 없습니다")
    return errors


def build_labels(notes: list[NoteEvent], duration: float) -> Labels:
    """노트 목록을 Basic Pitch 학습 라벨로 바꾼다.

    음높이 곡선 정답이 없으므로 contour는 각 음의 중심 칸으로 근사한다. 벤딩은 반영되지 않는다.
    """
    n_frames = math.ceil(duration * ANNOTATIONS_FPS) + 1
    note_cells: set[tuple[int, int]] = set()
    onset_cells: set[tuple[int, int]] = set()
    contour_cells: set[tuple[int, int]] = set()
    skipped = 0

    for note in notes:
        semitone = note.pitch - LOWEST_MIDI_PITCH
        first = round(note.start * ANNOTATIONS_FPS)
        if not 0 <= semitone < N_FREQ_BINS_NOTES or not 0 <= first < n_frames:
            skipped += 1
            continue

        last = max(first + 1, round(note.end * ANNOTATIONS_FPS))
        contour_bin = semitone * CONTOURS_BINS_PER_SEMITONE + CONTOURS_BINS_PER_SEMITONE // 2
        onset_cells.add((first, semitone))
        for frame in range(first, min(last, n_frames)):
            note_cells.add((frame, semitone))
            contour_cells.add((frame, contour_bin))

    return Labels(
        notes=sorted(note_cells),
        onsets=sorted(onset_cells),
        contours=sorted(contour_cells),
        n_frames=n_frames,
        skipped_notes=skipped,
    )


def read_midi_notes(path: Path) -> list[NoteEvent]:
    """MIDI에서 드럼을 뺀 모든 음을 시작 시간순으로 읽는다."""
    import pretty_midi

    midi = pretty_midi.PrettyMIDI(str(path))
    notes = [
        NoteEvent(start=float(note.start), end=float(note.end), pitch=int(note.pitch))
        for instrument in midi.instruments
        if not instrument.is_drum
        for note in instrument.notes
    ]
    return sorted(notes, key=lambda note: (note.start, note.pitch))
