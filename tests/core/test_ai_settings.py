import pytest

from app.core.config import Settings


def test_ai_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AI_SEPARATION_MODEL", "AI_MODEL_CACHE_DIR", "AI_CONFIG_VERSION"):
        monkeypatch.delenv(name, raising=False)

    s = Settings(_env_file=None)

    assert s.ai_config_version == "2026-11-27"
    assert s.ai_separation_model == "BS-Roformer-SW.ckpt"
    assert s.ai_separation_fallback == "htdemucs_6s"
    assert s.ai_transcription_weights == "icassp2022-onnx"
    assert s.ai_beat_model == "final0"
    assert s.ai_onset_threshold == 0.5
    assert s.ai_frame_threshold == 0.3
    assert s.ai_low_confidence == 0.4
    assert s.ai_min_note_length_ms == 58.0
    assert s.ai_min_frequency_hz == 80.0
    assert s.ai_max_frequency_hz == 1320.0
    assert s.ai_note_merge_gap_ms == 30
    assert s.ai_separation_rms_db == 30.0
    assert s.ai_tab_weights == {"span": 1.0, "pos": 0.1, "move": 0.5, "strskip": 0.3}
    assert s.ai_tab_max_fret == 22
    assert s.ai_tab_span_limit == 4
    assert s.ai_tab_span_penalty == 100.0
    assert s.ai_tab_max_states == 50
    assert s.ai_playable_max_span == 5
    assert s.ai_playable_max_move == 7
    assert s.ai_model_cache_dir == "/models"


def test_ai_settings_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SEPARATION_MODEL", "other.ckpt")
    monkeypatch.setenv("AI_ONSET_THRESHOLD", "0.7")
    monkeypatch.setenv("AI_MODEL_CACHE_DIR", "/tmp/models")
    monkeypatch.setenv("AI_TAB_WEIGHTS", '{"span": 2.0, "pos": 0.1, "move": 0.5, "strskip": 0.3}')

    s = Settings(_env_file=None)

    assert s.ai_separation_model == "other.ckpt"
    assert s.ai_onset_threshold == 0.7
    assert s.ai_model_cache_dir == "/tmp/models"
    assert s.ai_tab_weights["span"] == 2.0


def test_ai_packages_not_imported_by_app() -> None:
    """api·CI 환경 보호: app import만으로 AI 라이브러리가 로드되면 안 된다.

    다른 테스트가 AI 라이브러리를 이미 불러왔을 수 있어 새 프로세스에서 확인한다.
    """
    import subprocess
    import sys

    code = (
        "import sys, app.core.celery_app, app.main;"
        "ai = {'torch', 'basic_pitch', 'beat_this', 'audio_separator', 'librosa', 'music21'};"
        "print(sorted(ai & set(sys.modules)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip().splitlines()[-1] == "[]"
