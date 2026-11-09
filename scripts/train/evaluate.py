"""manifest의 test 곡으로 기존 모델과 학습 모델의 노트 F1을 비교한다.

기준은 설계서와 같다: 시작 ±50ms, 음높이 일치.
일렉 F1이 오르고 어쿠스틱 F1이 떨어지지 않을 때만 교체한다.
실행: python scripts/train/evaluate.py --manifest data/manifest.csv --candidate run/model.onnx
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import basic_pitch
import mir_eval
import numpy as np
import pretty_midi
from basic_pitch.inference import predict
from training_data import NoteEvent, Track, read_manifest, read_midi_notes

PUBLIC_ONNX = Path(basic_pitch.__file__).parent / "saved_models" / "icassp_2022" / "nmp.onnx"
ONSET_TOLERANCE_SECONDS = 0.05


def main() -> None:
    args = parse_args()
    tracks = [track for track in read_manifest(args.manifest) if track.split == "test"]
    rows = [evaluate_track(track, args) for track in tracks]

    if args.output:
        write_rows(rows, args.output)
    means = summarize(rows)
    print_summary(means)
    print("판정:", "교체 가능" if should_replace(means) else "교체하지 않음")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, default=PUBLIC_ONNX)
    parser.add_argument("--onset-threshold", type=float, default=0.5)
    parser.add_argument("--frame-threshold", type=float, default=0.3)
    parser.add_argument("--output", type=Path, help="곡별 결과 CSV 경로")
    return parser.parse_args()


def evaluate_track(track: Track, args: argparse.Namespace) -> dict[str, str | float]:
    reference = read_midi_notes(track.midi)
    baseline = note_f1(reference, transcribe(track, args.baseline, args))
    candidate = note_f1(reference, transcribe(track, args.candidate, args))
    print(f"{track.track_id} ({track.kind}): 기존 {baseline:.4f}, 학습 {candidate:.4f}")
    return {
        "track_id": track.track_id,
        "kind": track.kind,
        "baseline_f1": baseline,
        "candidate_f1": candidate,
    }


def transcribe(track: Track, model: Path, args: argparse.Namespace) -> list[NoteEvent]:
    _, _, events = predict(
        str(track.audio),
        str(model),
        onset_threshold=args.onset_threshold,
        frame_threshold=args.frame_threshold,
    )
    return [NoteEvent(float(event[0]), float(event[1]), int(event[2])) for event in events]


def note_f1(reference: list[NoteEvent], estimated: list[NoteEvent]) -> float:
    if not reference or not estimated:
        return 0.0
    _, _, f1, _ = mir_eval.transcription.precision_recall_f1_overlap(
        *_to_arrays(reference),
        *_to_arrays(estimated),
        onset_tolerance=ONSET_TOLERANCE_SECONDS,
        offset_ratio=None,
    )
    return float(f1)


def _to_arrays(notes: list[NoteEvent]) -> tuple[np.ndarray, np.ndarray]:
    intervals = np.array([(note.start, note.end) for note in notes])
    pitches = np.array([pretty_midi.note_number_to_hz(note.pitch) for note in notes])
    return intervals, pitches


def summarize(rows: list[dict[str, str | float]]) -> dict[str, tuple[float, float]]:
    """종류별 곡 평균 F1을 (기존, 학습)으로 돌려준다."""
    groups: defaultdict[str, list[dict[str, str | float]]] = defaultdict(list)
    for row in rows:
        groups[str(row["kind"])].append(row)
    return {
        kind: (
            float(np.mean([float(row["baseline_f1"]) for row in group])),
            float(np.mean([float(row["candidate_f1"]) for row in group])),
        )
        for kind, group in groups.items()
    }


def should_replace(means: dict[str, tuple[float, float]]) -> bool:
    if "electric" not in means:
        return False
    baseline, candidate = means["electric"]
    if candidate <= baseline:
        return False
    if "acoustic" not in means:
        return True
    baseline, candidate = means["acoustic"]
    return candidate >= baseline


def print_summary(means: dict[str, tuple[float, float]]) -> None:
    for kind, (baseline, candidate) in sorted(means.items()):
        print(f"{kind} 평균 F1: 기존 {baseline:.4f}, 학습 {candidate:.4f}")


def write_rows(rows: list[dict[str, str | float]], output: Path) -> None:
    with output.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
