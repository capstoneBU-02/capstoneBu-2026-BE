"""내 연주 파일을 판정 함수가 읽을 수 있는 WAV로 바꾼다.

REC는 녹음(브라우저 webm·m4a 등)과 업로드(mp3·wav 등)를 모두 받으므로,
판정 함수에는 항상 같은 형식(16bit PCM WAV, 설정 샘플레이트)만 넘긴다.
채널 수는 원본 그대로 둔다. ffmpeg가 모노로 합치면 좌우 위상이 반대인 녹음이 상쇄되어
소리가 있는데도 무음으로 판정될 수 있다 (무음 검사는 채널별 에너지로 한다).
"""

import subprocess

from app.domains.compare.pipeline.contract import InvalidAudioError
from app.domains.processing.pipeline.audio import rms_dbfs


def to_wav(src_path: str, out_path: str, *, sample_rate: int, silence_dbfs: float) -> None:
    """ffmpeg로 WAV로 바꾸고 무음인지 검사한다. 읽을 수 없거나 무음이면 InvalidAudioError."""
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
