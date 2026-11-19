import shutil
import subprocess
from pathlib import Path

import pytest

from app.domains.compare.pipeline import contract, performance_audio

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 없음")
sf = pytest.importorskip("soundfile")


def make(path: Path, source: str) -> None:
    """ffmpeg lavfi로 2초짜리 시험 음원을 만든다."""
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            source,
            "-t",
            "2",
            str(path),
        ],
        check=True,
    )


@pytest.mark.parametrize("ext", ["webm", "m4a", "mp3", "wav"])
def test_converts_to_mono_wav(tmp_path: Path, ext: str) -> None:
    src = tmp_path / f"perf.{ext}"
    make(src, "sine=frequency=330:sample_rate=48000")
    out = tmp_path / "out.wav"
    performance_audio.to_wav(str(src), str(out), sample_rate=44100, silence_dbfs=-50.0)
    info = sf.info(str(out))
    assert (info.samplerate, info.channels) == (44100, 1)
    assert 1.9 < info.duration < 2.1


def test_silent_performance_is_invalid(tmp_path: Path) -> None:
    src = tmp_path / "silent.wav"
    make(src, "anullsrc=sample_rate=44100")
    with pytest.raises(contract.InvalidAudioError, match="무음"):
        performance_audio.to_wav(
            str(src), str(tmp_path / "o.wav"), sample_rate=44100, silence_dbfs=-50.0
        )


def test_unreadable_file_is_invalid(tmp_path: Path) -> None:
    src = tmp_path / "broken.webm"
    src.write_bytes(b"not audio")
    with pytest.raises(contract.InvalidAudioError, match="디코딩"):
        performance_audio.to_wav(
            str(src), str(tmp_path / "o.wav"), sample_rate=44100, silence_dbfs=-50.0
        )
