"""환경 점검용 가짜 데이터(사인파 음원 + MIDI + manifest)를 만든다. 성능 평가에는 쓰지 않는다.

실행: python scripts/train/make_smoke_data.py --output /tmp/smoke
"""

import argparse
import csv
from pathlib import Path

import numpy as np
import pretty_midi
import soundfile
from training_data import AUDIO_SAMPLE_RATE

TRACKS = (
    ("train1", "song1", "train", "electric"),
    ("train2", "song2", "train", "electric"),
    ("train3", "song3", "train", "acoustic"),
    ("train4", "song4", "train", "acoustic"),
    ("valid1", "song5", "validation", "electric"),
    ("test1", "song6", "test", "electric"),
    ("test2", "song7", "test", "acoustic"),
)
DURATION_SECONDS = 8.0
NOTE_SECONDS = 0.4


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(0)
    with (args.output / "manifest.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(("track_id", "song", "split", "kind", "audio", "midi"))
        for track_id, song, split, kind in TRACKS:
            write_track(args.output, track_id, rng)
            writer.writerow((track_id, song, split, kind, f"{track_id}.wav", f"{track_id}.mid"))
    print("가짜 데이터 생성 완료:", args.output)


def write_track(output: Path, track_id: str, rng: np.random.Generator) -> None:
    n_notes = int(DURATION_SECONDS / NOTE_SECONDS)
    pitches = rng.integers(45, 80, n_notes)
    audio = np.zeros(int(DURATION_SECONDS * AUDIO_SAMPLE_RATE), dtype=np.float32)
    instrument = pretty_midi.Instrument(program=27)

    for i, pitch in enumerate(pitches):
        start = i * NOTE_SECONDS
        first = int(start * AUDIO_SAMPLE_RATE)
        t = np.arange(int(NOTE_SECONDS * AUDIO_SAMPLE_RATE)) / AUDIO_SAMPLE_RATE
        tone = np.sin(2 * np.pi * pretty_midi.note_number_to_hz(int(pitch)) * t)
        audio[first : first + len(t)] = 0.5 * tone * np.exp(-3 * t)
        instrument.notes.append(
            pretty_midi.Note(velocity=80, pitch=int(pitch), start=start, end=start + NOTE_SECONDS)
        )

    midi = pretty_midi.PrettyMIDI()
    midi.instruments.append(instrument)
    midi.write(str(output / f"{track_id}.mid"))
    soundfile.write(output / f"{track_id}.wav", audio, AUDIO_SAMPLE_RATE, subtype="PCM_16")


if __name__ == "__main__":
    main()
