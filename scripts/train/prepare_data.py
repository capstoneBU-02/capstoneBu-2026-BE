"""음원과 MIDI 정답을 Basic Pitch 학습 형식(TFRecord)으로 바꾼다. test 곡은 변환하지 않는다.

실행: python scripts/train/prepare_data.py --manifest data/manifest.csv --output /tmp/tfrecords
"""

import argparse
import io
from pathlib import Path

import librosa
import numpy as np
import soundfile
import tensorflow as tf
from basic_pitch import constants
from basic_pitch.data import tf_example_deserialization
from training_data import (
    ANNOTATIONS_FPS,
    AUDIO_SAMPLE_RATE,
    N_FREQ_BINS_CONTOURS,
    N_FREQ_BINS_NOTES,
    Labels,
    Track,
    build_labels,
    read_manifest,
    read_midi_notes,
)

MIN_DURATION_SECONDS = 3.0  # 학습 구간(2초)보다 충분히 길어야 무작위로 자를 수 있다
DATASET_NAME = "capstone"


def main() -> None:
    args = parse_args()
    check_constants()
    tracks = [track for track in read_manifest(args.manifest) if track.split != "test"]

    skipped = 0
    for track in tracks:
        skipped += write_track(track, args.output / DATASET_NAME / "splits" / track.split)

    print(f"변환 완료: {len(tracks)}곡, 범위를 벗어나 제외한 음 {skipped}개")
    check_batch(args.output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def check_constants() -> None:
    """Basic Pitch 버전이 바뀌어 라벨 계산이 어긋나는 일을 막는다."""
    expected = (AUDIO_SAMPLE_RATE, ANNOTATIONS_FPS, N_FREQ_BINS_NOTES, N_FREQ_BINS_CONTOURS)
    actual = (
        constants.AUDIO_SAMPLE_RATE,
        constants.ANNOTATIONS_FPS,
        constants.N_FREQ_BINS_NOTES,
        constants.N_FREQ_BINS_CONTOURS,
    )
    if expected != actual:
        raise RuntimeError(f"Basic Pitch 상수가 다릅니다: {expected} != {actual}")


def write_track(track: Track, directory: Path) -> int:
    audio, _ = librosa.load(track.audio, sr=AUDIO_SAMPLE_RATE, mono=True)
    duration = len(audio) / AUDIO_SAMPLE_RATE
    if duration < MIN_DURATION_SECONDS:
        raise ValueError(f"{track.track_id}: {MIN_DURATION_SECONDS}초 이상이어야 합니다")

    labels = build_labels(read_midi_notes(track.midi), duration)
    if not labels.notes:
        raise ValueError(f"{track.track_id}: 사용할 수 있는 음이 없습니다")

    directory.mkdir(parents=True, exist_ok=True)
    example = to_example(track.track_id, encode_wav(audio), labels)
    with tf.io.TFRecordWriter(str(directory / f"{track.track_id}.tfrecord")) as writer:
        writer.write(example.SerializeToString())
    return labels.skipped_notes


def encode_wav(audio: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    soundfile.write(buffer, audio, AUDIO_SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def to_example(track_id: str, wav: bytes, labels: Labels) -> tf.train.Example:
    """Basic Pitch의 tf_example_serialization과 같은 구조로 만든다."""
    features = {
        "file_id": bytes_feature(track_id.encode()),
        "source": bytes_feature(DATASET_NAME.encode()),
        "audio_wav": bytes_feature(wav),
        "notes_indices": indices_feature(labels.notes),
        "notes_values": values_feature(len(labels.notes)),
        "onsets_indices": indices_feature(labels.onsets),
        "onsets_values": values_feature(len(labels.onsets)),
        "contours_indices": indices_feature(labels.contours),
        "contours_values": values_feature(len(labels.contours)),
        "notes_onsets_shape": shape_feature(labels.n_frames, N_FREQ_BINS_NOTES),
        "contours_shape": shape_feature(labels.n_frames, N_FREQ_BINS_CONTOURS),
    }
    return tf.train.Example(features=tf.train.Features(feature=features))


def bytes_feature(value: bytes) -> tf.train.Feature:
    return tf.train.Feature(bytes_list=tf.train.BytesList(value=[value]))


def tensor_feature(value: np.ndarray) -> tf.train.Feature:
    return bytes_feature(tf.io.serialize_tensor(value).numpy())


def indices_feature(indices: list[tuple[int, int]]) -> tf.train.Feature:
    return tensor_feature(np.array(indices, dtype=np.int64).reshape(-1, 2))


def values_feature(count: int) -> tf.train.Feature:
    return tensor_feature(np.ones(count, dtype=np.float32))


def shape_feature(n_frames: int, n_bins: int) -> tf.train.Feature:
    return tensor_feature(np.array((n_frames, n_bins), dtype=np.int64))


def check_batch(output: Path) -> None:
    """변환한 파일을 Basic Pitch 데이터 로더로 실제로 읽어 본다."""
    train, _ = tf_example_deserialization.prepare_datasets(
        str(output), 16, 2, 1, [DATASET_NAME], np.array([1.0])
    )
    audio, target, _ = next(iter(train))
    shapes = {key: value.shape.as_list() for key, value in target.items()}
    print("데이터 확인:", audio.shape.as_list(), shapes)


if __name__ == "__main__":
    main()
