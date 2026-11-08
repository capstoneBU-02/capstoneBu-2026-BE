"""오디오 파일 공통 계산 (소리 크기, 소리 나는 구간 비율).

soundfile·numpy는 worker에만 있으므로 함수 안에서 import 한다.
"""

import math
from typing import Any

FRAME_MS = 50  # 소리 나는 구간을 판단하는 단위


def _read_mono(path: str) -> tuple[Any, int]:
    import numpy as np
    import soundfile as sf

    data, sr = sf.read(path, dtype="float32", always_2d=True)
    return np.mean(data, axis=1), int(sr)


def to_dbfs(rms: float) -> float:
    return 20 * math.log10(rms) if rms > 0 else -math.inf


def rms_dbfs(path: str) -> float:
    """파일 전체의 RMS 크기(dBFS). 무음이면 -inf."""
    import numpy as np

    mono, _ = _read_mono(path)
    if mono.size == 0:
        return -math.inf
    return to_dbfs(float(np.sqrt(np.mean(np.square(mono)))))


def active_ratio(path: str, *, silence_dbfs: float) -> float:
    """FRAME_MS 단위로 나눠 silence_dbfs보다 큰 구간의 비율 (0~1)."""
    import numpy as np

    mono, sr = _read_mono(path)
    frame = max(int(sr * FRAME_MS / 1000), 1)
    n = mono.size // frame
    if n == 0:
        return 0.0
    frames = mono[: n * frame].reshape(n, frame)
    rms = np.sqrt(np.mean(np.square(frames), axis=1))
    threshold = 10 ** (silence_dbfs / 20)
    return float(np.mean(rms > threshold))
