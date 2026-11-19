"""내 연주 파일을 판정 함수가 읽을 수 있는 WAV로 바꾼다.

REC는 녹음(브라우저 webm·m4a 등)과 업로드(mp3·wav 등)를 모두 받으므로,
판정 함수에는 항상 같은 형식(모노 PCM WAV, 설정 샘플레이트)만 넘긴다.
"""

import subprocess

from app.domains.compare.pipeline.contract import InvalidAudioError
from app.domains.processing.pipeline.audio import rms_dbfs


def to_wav(src_path: str, out_path: str, *, sample_rate: int, silence_dbfs: float) -> None:
    """ffmpeg로 모노 WAV로 바꾸고 무음인지 검사한다. 읽을 수 없거나 무음이면 InvalidAudioError."""
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        src_path,
        "-vn",  # 녹화 파일의 영상 트랙 제외
        "-ac",
        "1",
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
        raise InvalidAudioError(f"무음 연주 ({level:.1f} dBFS)")
