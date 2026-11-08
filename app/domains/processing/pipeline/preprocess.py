"""전처리: 구간 자르기(앞뒤 여유 포함), WAV 변환, 무음 검사."""

import subprocess
from dataclasses import dataclass

from app.domains.processing.pipeline.audio import rms_dbfs


class InvalidAudioError(Exception):
    """읽을 수 없거나 무음인 입력. 다시 실행해도 결과가 같으므로 재시도하지 않는다."""


@dataclass(frozen=True)
class PreprocessResult:
    pad_before_ms: int  # 앞쪽 여유 (구간 시작이 0에 가까우면 줄어든다)
    rms_dbfs: float


def preprocess(
    src_path: str,
    start_ms: int,
    end_ms: int,
    out_path: str,
    *,
    padding_ms: int,
    sample_rate: int,
    silence_dbfs: float,
) -> PreprocessResult:
    if start_ms < 0 or end_ms <= start_ms:
        raise InvalidAudioError(f"잘못된 구간 범위: {start_ms}~{end_ms}ms")

    pad_before_ms = min(padding_ms, start_ms)
    seek_ms = start_ms - pad_before_ms
    length_ms = pad_before_ms + (end_ms - start_ms) + padding_ms
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{seek_ms / 1000:.3f}",
        "-t",
        f"{length_ms / 1000:.3f}",
        "-i",
        src_path,
        "-vn",  # MP4 영상 트랙 제외
        "-ac",
        "2",
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise InvalidAudioError(f"디코딩 실패: {proc.stderr.strip()[:300]}")

    level = rms_dbfs(out_path)
    if level < silence_dbfs:
        raise InvalidAudioError(f"무음 구간 ({level:.1f} dBFS)")
    return PreprocessResult(pad_before_ms=pad_before_ms, rms_dbfs=level)
