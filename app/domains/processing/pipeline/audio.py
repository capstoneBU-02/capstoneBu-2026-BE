"""오디오 파일 공통 계산 (소리 크기, 소리 나는 구간 비율).

soundfile·numpy는 worker에만 있으므로 함수 안에서 import 한다.
에너지는 채널별 제곱 평균으로 계산한다. 좌우를 먼저 평균 내면 위상이 반대인 스테레오가 상쇄된다.
"""

import math
from typing import Any

FRAME_MS = 50  # 소리 나는 구간을 판단하는 단위


def read_range(path: str, start_ms: int = 0, duration_ms: int | None = None) -> tuple[Any, int]:
    """(프레임 수, 채널 수) 배열과 샘플레이트. start_ms부터 duration_ms만큼 (None이면 끝까지)."""
    import soundfile as sf

    sr = int(sf.info(path).samplerate)
    start = int(sr * start_ms / 1000)
    stop = None if duration_ms is None else start + int(sr * duration_ms / 1000)
    data, _ = sf.read(path, start=start, stop=stop, dtype="float32", always_2d=True)
    return data, sr


def to_dbfs(rms: float) -> float:
    """RMS(0~1)를 dBFS로. 0이면 -inf."""
    return 20 * math.log10(rms) if rms > 0 else -math.inf


def _rms(data: Any) -> float:
    """모든 채널의 제곱 평균 제곱근."""
    import numpy as np

    return float(np.sqrt(np.mean(np.square(data)))) if data.size else 0.0


def rms_dbfs(path: str, start_ms: int = 0, duration_ms: int | None = None) -> float:
    """지정 범위의 RMS 크기(dBFS). 모든 채널의 에너지를 합쳐 계산한다. 무음이면 -inf."""
    data, _ = read_range(path, start_ms, duration_ms)
    return to_dbfs(_rms(data))


def active_ratio(path: str, *, silence_dbfs: float) -> float:
    """FRAME_MS 단위로 나눠 silence_dbfs보다 큰 구간의 비율 (0~1).

    한 프레임보다 짧은 오디오는 전체 RMS 하나로 판정한다.
    """
    import numpy as np

    data, sr = read_range(path)
    if data.size == 0:
        return 0.0
    threshold = 10 ** (silence_dbfs / 20)
    frame = max(int(sr * FRAME_MS / 1000), 1)
    n = data.shape[0] // frame
    if n == 0:
        return 1.0 if _rms(data) > threshold else 0.0
    frames = data[: n * frame].reshape(n, frame, data.shape[1])
    rms = np.sqrt(np.mean(np.square(frames), axis=(1, 2)))
    return float(np.mean(rms > threshold))
