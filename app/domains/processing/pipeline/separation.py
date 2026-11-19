"""기타 분리: BS-RoFormer-SW (대체 Demucs htdemucs_6s), audio-separator로 실행.

입력은 앞뒤 여유를 포함한 구간 WAV이고, 결과는 여유를 잘라 구간 길이에 맞춘다.
두 모델 모두 6개 악기(보컬·드럼·베이스·기타·피아노·그 외)로 나눈다. 기타는 TAB·CMP 입력이고,
나머지 악기는 각각 저장하며 합쳐서 기타를 뺀 반주(backing)를 만든다.
"""

import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from celery.exceptions import SoftTimeLimitExceeded

from app.domains.processing.pipeline.audio import active_ratio, read_range, rms_dbfs

logger = logging.getLogger(__name__)

__all__ = [
    "NoGuitarError",
    "SeparationResult",
    "ShortAudioError",
    "active_ratio",
    "mix",
    "separate_guitar",
    "trim",
]

GUITAR = "guitar"
BACKING = "backing"  # 기타를 뺀 나머지 악기를 합친 반주
# 출력 파일 이름의 괄호 안 악기 이름
# (예: input_(bass)_BS-Roformer-SW.wav, input_(Guitar)_htdemucs_6s.wav)
_PART_RE = re.compile(r"\(([A-Za-z]+)\)")

# 디코딩·리샘플링에서 생기는 끝부분 오차는 허용한다
TRIM_TOLERANCE_MS = 20


class NoGuitarError(Exception):
    """기타 스템이 입력보다 너무 작다. 다시 실행해도 결과가 같으므로 재시도하지 않는다."""


class ShortAudioError(Exception):
    """잘라 낼 범위보다 오디오가 짧다 (구간이 음원 길이를 넘음)."""


@dataclass(frozen=True)
class SeparationResult:
    """분리에 쓴 모델과 같은 구간의 입력·기타 크기."""

    model: str
    input_dbfs: float
    guitar_dbfs: float
    # 기타를 뺀 악기와 반주의 잘라 낸 파일 경로 (악기 이름 → 경로). parts_dir가 없으면 비어 있음
    parts: dict[str, str] = field(default_factory=dict)


def trim(src_path: str, out_path: str, start_ms: int, duration_ms: int) -> None:
    """src의 start_ms부터 duration_ms만큼 잘라 WAV로 저장한다. 모자라면 ShortAudioError."""
    import soundfile as sf

    data, sr = read_range(src_path, start_ms, duration_ms)
    expected = int(sr * duration_ms / 1000)
    tolerance = int(sr * TRIM_TOLERANCE_MS / 1000)
    if data.shape[0] < expected - tolerance:
        got_ms = data.shape[0] * 1000 // sr
        raise ShortAudioError(f"요청 {duration_ms}ms, 실제 {got_ms}ms")
    sf.write(out_path, data, sr, subtype="PCM_16")


def _model_filename(name: str) -> str:
    """설정의 모델 이름을 audio-separator가 찾는 파일 이름으로.

    Demucs 모델은 yaml 이름으로 찾는다 (예: htdemucs_6s → htdemucs_6s.yaml).
    """
    return name if Path(name).suffix else f"{name}.yaml"


def part_name(path: Path) -> str | None:
    """출력 파일 이름에서 악기 이름(소문자)을 읽는다. 없으면 None."""
    m = _PART_RE.search(path.name)
    return m.group(1).lower() if m else None


def _run_separator(in_path: str, out_dir: str, model: str, model_dir: str) -> dict[str, Path]:
    """audio-separator로 모든 악기를 뽑아 악기 이름 → 파일 경로를 돌려준다. 기타가 없으면 오류."""
    from audio_separator.separator import Separator

    separator = Separator(model_file_dir=model_dir, output_dir=out_dir, output_format="WAV")
    separator.load_model(model_filename=_model_filename(model))
    outputs = separator.separate(in_path)
    parts: dict[str, Path] = {}
    for name in outputs:
        path = Path(name) if Path(name).is_absolute() else Path(out_dir) / name
        part = part_name(path)
        if part:
            parts[part] = path
    if GUITAR not in parts:
        raise RuntimeError(f"기타 스템 파일이 없습니다: {outputs}")
    return parts


def mix(paths: list[str], out_path: str) -> None:
    """같은 길이로 잘린 WAV들을 더해 하나로 저장한다. 길이가 다르면 짧은 쪽에 맞추고 클리핑한다."""
    import numpy as np
    import soundfile as sf

    if not paths:
        raise ValueError("합칠 파일이 없습니다")
    arrays = []
    sr = 0
    for p in paths:
        data, sr = read_range(p)
        arrays.append(data)
    n = min(a.shape[0] for a in arrays)
    channels = max(a.shape[1] for a in arrays)
    total = np.zeros((n, channels), dtype="float32")
    for a in arrays:
        total += a[:n] if a.shape[1] == channels else np.repeat(a[:n, :1], channels, axis=1)
    sf.write(out_path, np.clip(total, -1.0, 1.0), sr, subtype="PCM_16")


def separate_guitar(
    in_path: str,
    out_path: str,
    *,
    model: str,
    model_dir: str,
    trim_start_ms: int,
    duration_ms: int,
    no_guitar_db: float,
    fallback_model: str | None = None,
    parts_dir: str | None = None,
) -> SeparationResult:
    """기타 스템을 뽑아 구간 길이로 자른다. 기타가 너무 작으면 NoGuitarError.

    parts_dir가 있으면 기타를 뺀 악기도 같은 길이로 잘라 `{악기}.wav`로 두고,
    그 악기들을 합친 반주를 `backing.wav`로 둔다.
    """
    with tempfile.TemporaryDirectory(prefix="sep-") as out_dir:
        used = model
        try:
            stems = _run_separator(in_path, out_dir, model, model_dir)
        except SoftTimeLimitExceeded:
            raise  # 시간 초과는 대체 모델로 넘기지 않고 작업 실패로 처리한다
        except Exception:
            if not fallback_model:
                raise
            logger.warning("분리 모델 %s 실패 — 대체 모델 %s 사용", model, fallback_model)
            used = fallback_model
            stems = _run_separator(in_path, out_dir, fallback_model, model_dir)
        trim(str(stems[GUITAR]), out_path, trim_start_ms, duration_ms)

        parts: dict[str, str] = {}
        if parts_dir is not None:
            for part, path in sorted(stems.items()):
                if part == GUITAR:
                    continue
                dst = str(Path(parts_dir) / f"{part}.wav")
                trim(str(path), dst, trim_start_ms, duration_ms)
                parts[part] = dst
            if parts:
                backing = str(Path(parts_dir) / f"{BACKING}.wav")
                mix(list(parts.values()), backing)
                parts[BACKING] = backing

    # 기타와 같은 구간(여유 제외)끼리 비교한다
    input_db = rms_dbfs(in_path, trim_start_ms, duration_ms)
    guitar_db = rms_dbfs(out_path)
    if guitar_db < input_db - no_guitar_db:
        raise NoGuitarError(f"기타 {guitar_db:.1f} dBFS, 입력 {input_db:.1f} dBFS")
    return SeparationResult(model=used, input_dbfs=input_db, guitar_dbfs=guitar_db, parts=parts)
