import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType

import pytest

np = pytest.importorskip("numpy")
sf = pytest.importorskip("soundfile")


def load_module() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts" / "separation" / "evaluate.py"
    spec = importlib.util.spec_from_file_location("separation_evaluate", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ev = load_module()
SR = 8000


def tone(freq: float, amp: float = 0.3, seconds: float = 1.0, channels: int = 2) -> object:
    t = np.arange(int(SR * seconds)) / SR
    mono = (amp * np.sin(2 * np.pi * freq * t)).astype("float32")
    return np.repeat(mono[:, None], channels, axis=1)


def write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), data, SR, subtype="FLOAT")


def test_sdr_perfect_and_scaled() -> None:
    ref = tone(440)
    assert ev.sdr(ref, ref) > 80
    # 절반 크기: 오차 에너지가 정답의 1/4 → 약 6.02 dB
    assert ev.sdr(ref, ref * 0.5) == pytest.approx(20 * math.log10(2), abs=0.01)
    # SI-SDR은 크기 차이를 무시한다
    assert ev.si_sdr(ref, ref * 0.5) > 80


def test_sdr_matches_length_and_channels() -> None:
    ref = tone(440, channels=2)
    est = tone(440, channels=1, seconds=0.5)
    assert ev.sdr(ref, est) > 80


def test_noise_lowers_score() -> None:
    ref = tone(440)
    rng = np.random.default_rng(0)
    noisy = ref + rng.normal(0, 0.1, ref.shape).astype("float32")
    assert ev.sdr(ref, noisy) < ev.sdr(ref, ref * 0.99)


def test_silent_reference_is_not_scored(tmp_path: Path) -> None:
    rows = ev.score_song("s", {"piano": tone(440) * 0}, {"piano": tone(440)})
    assert rows == [{"song": "s", "part": "piano", "sdr": None, "si_sdr": None}]
    assert ev.summarize(rows) == {}


def test_main_scores_estimates_and_backing(tmp_path: Path) -> None:
    guitar, bass, drums = tone(330), tone(110), tone(60, amp=0.2)
    data, est = tmp_path / "data", tmp_path / "est"
    write(data / "song1" / "mix.wav", guitar + bass + drums)
    write(data / "song1" / "guitar.wav", guitar)
    write(data / "song1" / "bass.wav", bass)
    # 정답이 없는 악기(drums)는 채점하지 않지만 반주에는 들어간다
    write(est / "song1" / "guitar.wav", guitar)
    write(est / "song1" / "bass.wav", bass * 0.5)
    write(est / "song1" / "drums.wav", drums)
    (tmp_path / "data" / "notes").mkdir()  # 악기 파일 없는 폴더는 무시

    summary = ev.main(
        ["--data", str(data), "--output", str(tmp_path / "out"), "--estimates", str(est)]
    )

    assert summary["songs"] == 1
    assert set(summary["parts"]) == {"guitar", "bass", "backing"}
    assert summary["parts"]["guitar"]["sdr_median"] > 80
    assert summary["parts"]["bass"]["sdr_median"] == pytest.approx(6.021, abs=0.01)
    assert summary["parts"]["backing"]["sdr_median"] > 5
    saved = json.loads((tmp_path / "out" / "summary.json").read_text(encoding="utf-8"))
    assert saved["parts"] == summary["parts"]
    lines = (tmp_path / "out" / "scores.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "song,part,sdr,si_sdr" and len(lines) == 4


def test_mismatched_sample_rate_fails(tmp_path: Path) -> None:
    song, est = tmp_path / "data" / "song1", tmp_path / "est" / "song1"
    write(song / "guitar.wav", tone(330))
    write(est / "guitar.wav", tone(330))
    ev.check_rates(song, est)
    sf.write(str(est / "bass.wav"), tone(110), 16000)
    with pytest.raises(ValueError, match="샘플레이트"):
        ev.check_rates(song, est)


def test_main_without_songs_exits(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        ev.main(["--data", str(tmp_path), "--output", str(tmp_path / "out"), "--estimates", "x"])
