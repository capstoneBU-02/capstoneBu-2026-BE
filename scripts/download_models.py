"""AI 모델 가중치를 AI_MODEL_CACHE_DIR에 받는다. 이미 있는 파일은 건너뛴다.

실행 (worker 컨테이너, model-cache 볼륨에 저장):
    docker compose run --rm worker python scripts/download_models.py

받는 모델은 설정값을 따른다: AI_SEPARATION_MODEL(audio-separator), AI_BEAT_MODEL(Beat This!)
라이선스는 docs/MODELS.md 참고.
"""

import hashlib
import sys
from pathlib import Path

# scripts/에서 바로 실행해도 app 패키지를 찾도록
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download_separation_model(model_name: str, target_dir: Path) -> None:
    from audio_separator.separator import Separator  # AI 패키지는 지연 import

    target_dir.mkdir(parents=True, exist_ok=True)
    # 모델 파일과 설정(yaml)이 이미 있으면 audio-separator가 다운로드를 건너뛴다
    Separator(model_file_dir=str(target_dir), info_only=True).download_model_and_data(model_name)


def download_beat_model(model_name: str, target_dir: Path) -> None:
    import torch
    from beat_this.inference import CHECKPOINT_URL

    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"beat_this-{model_name}.ckpt"
    if path.exists():
        print(f"건너뜀 (이미 있음): {path}")
        return
    torch.hub.download_url_to_file(f"{CHECKPOINT_URL}/{model_name}.ckpt", str(path))


def main() -> None:
    settings = get_settings()
    cache_dir = Path(settings.ai_model_cache_dir)
    separation_dir = cache_dir / "audio-separator"
    beat_dir = cache_dir / "beat_this"

    print(f"모델 캐시: {cache_dir} (config_version={settings.ai_config_version})")
    download_separation_model(settings.ai_separation_model, separation_dir)
    download_beat_model(settings.ai_beat_model, beat_dir)

    print("\nSHA-256")
    for path in sorted(p for d in (separation_dir, beat_dir) for p in d.iterdir() if p.is_file()):
        print(f"{sha256(path)}  {path}")


if __name__ == "__main__":
    main()
