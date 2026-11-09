"""학습한 가중치를 서비스용 ONNX로 바꾸고, 변환 전후 결과가 같은지 확인한다.

tf2onnx가 protobuf를 올려 학습 환경을 망가뜨릴 수 있으므로 별도 환경에서 실행한다.
실행: python scripts/train/export_onnx.py --weights run/best.weights.h5 --output run/model.onnx
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime
from basic_pitch import models
from basic_pitch.constants import AUDIO_N_SAMPLES

OPSET = 13
MAX_DIFFERENCE = 1e-3


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    model = models.model()
    model.load_weights(args.weights)
    with tempfile.TemporaryDirectory() as directory:
        model.save(directory)
        convert(directory, args.output)
    compare(model, args.output)
    print(f"ONNX 저장 완료: {args.output}")


def convert(saved_model: str, output: Path) -> None:
    command = [sys.executable, "-m", "tf2onnx.convert", "--saved-model", saved_model]
    command += ["--output", str(output), "--opset", str(OPSET)]
    subprocess.run(command, check=True)


def compare(model: Any, output: Path) -> None:
    x = np.random.default_rng(1).normal(0, 0.1, (1, AUDIO_N_SAMPLES, 1)).astype(np.float32)
    expected = model(x, training=False)
    session = onnxruntime.InferenceSession(str(output), providers=["CPUExecutionProvider"])
    names = [item.name for item in session.get_outputs()]
    actual = dict(zip(names, session.run(None, {session.get_inputs()[0].name: x}), strict=True))
    for key in sorted(expected):
        difference = float(np.max(np.abs(expected[key].numpy() - actual[key])))
        print(key, "최대 차이:", difference)
        if difference > MAX_DIFFERENCE:
            raise RuntimeError(f"변환 전후 결과가 다릅니다: {key} {difference}")


if __name__ == "__main__":
    main()
