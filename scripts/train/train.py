"""Basic Pitch 미세조정. 공개 가중치에서 시작하고, 끊기면 저장한 가중치에서 이어서 학습한다.

처음 실행: python scripts/train/train.py --data /tmp/tfrecords --output /kaggle/working/run
이어서 실행: 같은 명령에 --resume 추가

공개 train.py는 항상 새 모델로 시작하므로 가중치 불러오기를 여기서 맡는다.
옵티마이저 상태는 저장하지 않으므로 이어서 학습하면 Adam 내부 상태는 새로 시작한다.
"""

import argparse
import json
import os
import tempfile
from pathlib import Path

import basic_pitch
import numpy as np
import tensorflow as tf
from basic_pitch import models
from basic_pitch.constants import AUDIO_N_SAMPLES
from basic_pitch.data import tf_example_deserialization
from prepare_data import DATASET_NAME

PUBLIC_MODEL = Path(basic_pitch.__file__).parent / "saved_models" / "icassp_2022" / "nmp"
LAST_WEIGHTS = "last.weights.h5"
BEST_WEIGHTS = "best.weights.h5"
STATE_FILE = "state.json"
HISTORY_FILE = "history.csv"
MAX_COPY_DIFFERENCE = 1e-4


def main() -> None:
    args = parse_args()
    args.data, args.output = args.data.resolve(), args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    print("GPU:", tf.config.list_physical_devices("GPU"))

    # 공개 데이터 로더가 현재 폴더에 검증 캐시를 만들므로 결과 폴더 밖으로 옮긴다
    os.chdir(tempfile.mkdtemp())

    state = load_state(args.output) if args.resume else {"epochs": 0, "best_loss": None}
    model = build_model(args.output if args.resume else None, args.learning_rate)
    train_ds, validation_ds = tf_example_deserialization.prepare_datasets(
        str(args.data),
        args.shuffle_size,
        args.batch_size,
        args.validation_steps,
        [DATASET_NAME],
        np.array([1.0]),
    )
    model.fit(
        train_ds,
        initial_epoch=state["epochs"],
        epochs=state["epochs"] + args.epochs,
        steps_per_epoch=args.steps_per_epoch,
        validation_data=validation_ds,
        validation_steps=args.validation_steps,
        callbacks=build_callbacks(args, state),
    )
    print("학습 종료:", load_state(args.output))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True, help="prepare_data.py 결과 폴더")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--epochs", type=int, default=50, help="이번 실행에서 더 돌릴 에폭 수")
    parser.add_argument("--steps-per-epoch", type=int, default=100)
    parser.add_argument("--validation-steps", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--shuffle-size", type=int, default=100)
    parser.add_argument("--patience", type=int, default=5)
    return parser.parse_args()


def build_model(resume_dir: Path | None, learning_rate: float) -> tf.keras.Model:
    model = models.model()
    if resume_dir is None:
        copy_public_weights(model)
    else:
        model.load_weights(resume_dir / LAST_WEIGHTS)
    model.compile(
        loss=models.loss(),
        optimizer=tf.keras.optimizers.Adam(learning_rate),
        sample_weight_mode={"contour": None, "note": None, "onset": None},
    )
    return model


def copy_public_weights(model: tf.keras.Model) -> None:
    """공개 가중치를 순서대로 복사하고, 같은 입력에 같은 결과가 나오는지 확인한다.

    변수 이름 일부가 달라 이름으로는 복사할 수 없다.
    """
    public = tf.saved_model.load(str(PUBLIC_MODEL))
    source, target = list(public.variables), list(model.weights)
    if [v.shape for v in source] != [v.shape for v in target]:
        raise RuntimeError("공개 가중치와 학습 모델의 변수 구조가 다릅니다")
    for from_variable, to_variable in zip(source, target, strict=True):
        to_variable.assign(from_variable)

    x = np.random.default_rng(0).normal(0, 0.1, (1, AUDIO_N_SAMPLES, 1)).astype(np.float32)
    expected, actual = public(x), model(x, training=False)
    for key in expected:
        difference = float(tf.reduce_max(tf.abs(expected[key] - actual[key])))
        if difference > MAX_COPY_DIFFERENCE:
            raise RuntimeError(f"공개 가중치 복사 결과가 다릅니다: {key} {difference}")
    print("공개 가중치 복사 확인 완료")


def build_callbacks(
    args: argparse.Namespace, state: dict[str, float | int | None]
) -> list[tf.keras.callbacks.Callback]:
    best_loss = state["best_loss"]
    return [
        tf.keras.callbacks.ModelCheckpoint(
            str(args.output / BEST_WEIGHTS),
            save_weights_only=True,
            save_best_only=True,
            initial_value_threshold=best_loss,
        ),
        tf.keras.callbacks.ModelCheckpoint(str(args.output / LAST_WEIGHTS), save_weights_only=True),
        tf.keras.callbacks.CSVLogger(str(args.output / HISTORY_FILE), append=True),
        tf.keras.callbacks.EarlyStopping(patience=args.patience, verbose=1),
        tf.keras.callbacks.ReduceLROnPlateau(patience=2, factor=0.5, verbose=1),
        SaveState(args.output, best_loss),
    ]


class SaveState(tf.keras.callbacks.Callback):
    """에폭마다 진행 상황을 저장한다. 세션이 끊겨도 --resume으로 이어 갈 수 있다."""

    def __init__(self, output: Path, best_loss: float | None) -> None:
        super().__init__()
        self.output = output
        self.best_loss = best_loss

    def on_epoch_end(self, epoch: int, logs: dict[str, float] | None = None) -> None:
        loss = float((logs or {})["val_loss"])
        if self.best_loss is None or loss < self.best_loss:
            self.best_loss = loss
        state = {"epochs": epoch + 1, "best_loss": self.best_loss}
        (self.output / STATE_FILE).write_text(json.dumps(state), encoding="utf-8")


def load_state(output: Path) -> dict[str, float | int | None]:
    state: dict[str, float | int | None] = json.loads(
        (output / STATE_FILE).read_text(encoding="utf-8")
    )
    return state


if __name__ == "__main__":
    main()
