"""#85 곡 끝까지 잡은 구간: 길이 추정 오차는 허용하고 실제로 모자라면 막는다."""

from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
sf = pytest.importorskip("soundfile")

from app.domains.processing.pipeline import separation  # noqa: E402

SR = 44100


def _wav(path: Path, ms: int) -> str:
    sf.write(path, np.zeros((SR * ms // 1000, 2), dtype="float32"), SR, subtype="PCM_16")
    return str(path)


def test_trim_allows_mp3_length_estimate_error(tmp_path: Path) -> None:
    """기록된 길이보다 38ms 짧은 실제 오디오(공급자.mp3 사례)도 자른다."""
    src = _wav(tmp_path / "src.wav", 30_000 - 38)
    out = tmp_path / "out.wav"
    separation.trim(src, str(out), 0, 30_000)
    assert out.exists()


def test_trim_rejects_range_beyond_audio(tmp_path: Path) -> None:
    """구간이 오디오보다 허용 오차를 넘게 길면 실패한다."""
    src = _wav(tmp_path / "src.wav", 29_000)
    with pytest.raises(separation.ShortAudioError):
        separation.trim(src, str(tmp_path / "out.wav"), 0, 30_000)
