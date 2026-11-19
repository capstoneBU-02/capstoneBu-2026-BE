"""음원 분리 품질을 악기별 점수(SDR·SI-SDR, 단위 dB)로 잰다. 학습 전후 비교용.

평가 곡 폴더 구조 (곡마다 섞인 음원 mix.wav와 정답 악기 파일):
    data/
    ├── song1/mix.wav
    ├── song1/guitar.wav   # 악기 이름: vocals, drums, bass, guitar, piano, other
    └── song1/bass.wav     # 없는 악기는 빼도 된다 (그 악기는 채점하지 않음)

실행 (worker 컨테이너, 지금 설정의 분리 모델로 분리한 뒤 채점):
    docker compose run --rm worker python scripts/separation/evaluate.py \
        --data scripts/separation/data --output scripts/separation/runs/base

이미 분리한 결과만 채점 (학습한 모델 결과 등, 폴더 구조는 data와 같고 mix.wav는 없어도 됨):
    python scripts/separation/evaluate.py --data DATA --output OUT --estimates EST

결과: OUT/scores.csv (곡·악기별 점수), OUT/summary.json (악기별 중앙값·평균)
반주(backing)는 서비스와 같게 기타를 뺀 분리 결과를 합친 것이고, 정답은 mix - guitar다.
"""

import argparse
import csv
import json
import math
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

# scripts/에서 바로 실행해도 app 패키지를 찾도록
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

PARTS = ("vocals", "drums", "bass", "guitar", "piano", "other")
GUITAR = "guitar"
BACKING = "backing"
MIX = "mix.wav"
# 정답이 이보다 작으면 거의 무음이라 점수가 의미 없으므로 채점하지 않는다
SILENT_DBFS = -60.0
EPS = 1e-10


def read(path: Path) -> tuple[np.ndarray, int]:
    """(프레임 수, 채널 수) float32 배열과 샘플레이트."""
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return data, int(sr)


def match(ref: np.ndarray, est: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """길이는 짧은 쪽에 맞추고, 채널 수가 다르면 모노 쪽을 복사해 맞춘다."""
    n = min(ref.shape[0], est.shape[0])
    ref, est = ref[:n], est[:n]
    if ref.shape[1] != est.shape[1]:
        channels = max(ref.shape[1], est.shape[1])
        ref = ref if ref.shape[1] == channels else np.repeat(ref[:, :1], channels, axis=1)
        est = est if est.shape[1] == channels else np.repeat(est[:, :1], channels, axis=1)
    return ref, est


def is_silent(ref: np.ndarray) -> bool:
    """정답 음원이 거의 무음인지."""
    rms = float(np.sqrt(np.mean(np.square(ref)))) if ref.size else 0.0
    return rms <= 10 ** (SILENT_DBFS / 20)


def sdr(ref: np.ndarray, est: np.ndarray) -> float:
    """SDR (MDX 대회 방식): 정답 에너지 / 오차 에너지. 클수록 좋다."""
    ref, est = match(ref, est)
    num = float(np.sum(np.square(ref, dtype=np.float64)))
    den = float(np.sum(np.square(ref.astype(np.float64) - est)))
    return 10 * math.log10((num + EPS) / (den + EPS))


def si_sdr(ref: np.ndarray, est: np.ndarray) -> float:
    """SI-SDR: 크기 차이는 빼고 모양만 비교한다. 채널을 이어 붙여 하나로 계산한다."""
    ref, est = match(ref, est)
    r = ref.astype(np.float64).ravel()
    e = est.astype(np.float64).ravel()
    alpha = float(np.dot(e, r)) / (float(np.dot(r, r)) + EPS)
    target = alpha * r
    noise = e - target
    return 10 * math.log10(
        (float(np.dot(target, target)) + EPS) / (float(np.dot(noise, noise)) + EPS)
    )


def references(song_dir: Path) -> dict[str, np.ndarray]:
    """정답 악기들. 기타 정답과 mix가 있으면 반주 정답(mix - guitar)도 만든다."""
    refs = {p: read(song_dir / f"{p}.wav")[0] for p in PARTS if (song_dir / f"{p}.wav").exists()}
    mix_path = song_dir / MIX
    if GUITAR in refs and mix_path.exists():
        m, g = match(read(mix_path)[0], refs[GUITAR])
        refs[BACKING] = m - g
    return refs


def check_rates(*dirs: Path) -> None:
    """정답과 분리 결과의 샘플레이트가 모두 같은지. 다르면 점수가 의미 없으므로 오류."""
    rates = {int(sf.info(str(p)).samplerate) for d in dirs for p in d.glob("*.wav")}
    if len(rates) > 1:
        raise ValueError(f"{dirs[0].name}: 샘플레이트가 서로 다릅니다 {sorted(rates)}")


def mix_parts(parts: list[np.ndarray]) -> np.ndarray:
    """악기들을 더한다. 길이는 가장 짧은 쪽, 채널은 가장 많은 쪽에 맞추고 클리핑한다."""
    n = min(a.shape[0] for a in parts)
    channels = max(a.shape[1] for a in parts)
    total = np.zeros((n, channels), dtype=np.float32)
    for a in parts:
        total += a[:n] if a.shape[1] == channels else np.repeat(a[:n, :1], channels, axis=1)
    return np.clip(total, -1.0, 1.0)


def estimates(est_dir: Path) -> dict[str, np.ndarray]:
    """분리 결과. 반주 파일이 없으면 기타를 뺀 악기를 합쳐 만든다 (서비스와 같은 방식)."""
    ests = {
        p: read(est_dir / f"{p}.wav")[0]
        for p in (*PARTS, BACKING)
        if (est_dir / f"{p}.wav").exists()
    }
    others = [v for k, v in ests.items() if k not in (GUITAR, BACKING)]
    if BACKING not in ests and others:
        ests[BACKING] = mix_parts(others)
    return ests


def score_song(
    song: str, refs: dict[str, np.ndarray], ests: dict[str, np.ndarray]
) -> list[dict[str, Any]]:
    """정답과 분리 결과가 모두 있는 악기만 채점한다. 무음 정답은 점수를 비워 둔다."""
    rows: list[dict[str, Any]] = []
    for part in (*PARTS, BACKING):
        if part not in refs or part not in ests:
            continue
        silent = is_silent(refs[part])
        rows.append(
            {
                "song": song,
                "part": part,
                "sdr": None if silent else round(sdr(refs[part], ests[part]), 3),
                "si_sdr": None if silent else round(si_sdr(refs[part], ests[part]), 3),
            }
        )
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    """악기별 곡 수와 점수 중앙값·평균 (무음으로 빠진 곡 제외)."""
    out: dict[str, dict[str, float | int]] = {}
    for part in (*PARTS, BACKING):
        vals = [r for r in rows if r["part"] == part and r["sdr"] is not None]
        if not vals:
            continue
        s = [r["sdr"] for r in vals]
        si = [r["si_sdr"] for r in vals]
        out[part] = {
            "songs": len(vals),
            "sdr_median": round(statistics.median(s), 3),
            "sdr_mean": round(statistics.fmean(s), 3),
            "si_sdr_median": round(statistics.median(si), 3),
        }
    return out


def separate(mix_path: Path, dst: Path, model: str, model_dir: str) -> float:
    """mix를 분리해 dst/{악기}.wav로 저장하고 걸린 초를 돌려준다."""
    from app.domains.processing.pipeline.separation import _run_separator

    dst.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="eval-sep-") as tmp:
        for part, path in _run_separator(str(mix_path), tmp, model, model_dir).items():
            shutil.copyfile(path, dst / f"{part}.wav")
    return time.monotonic() - started


def song_dirs(data: Path) -> list[Path]:
    """정답 악기 파일이 하나라도 있는 곡 폴더 (이름순)."""
    return sorted(
        d for d in data.iterdir() if d.is_dir() and any((d / f"{p}.wav").exists() for p in PARTS)
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--data", type=Path, required=True, help="평가 곡 폴더")
    parser.add_argument("--output", type=Path, required=True, help="결과 폴더")
    parser.add_argument("--estimates", type=Path, help="이미 분리한 결과 폴더 (주면 분리하지 않음)")
    parser.add_argument("--model", help="분리 모델 (기본: AI_SEPARATION_MODEL)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> dict[str, Any]:
    args = parse_args(argv)
    songs = song_dirs(args.data)
    if not songs:
        raise SystemExit(f"{args.data}에 평가할 곡이 없습니다 (곡 폴더 안에 guitar.wav 등 필요)")
    args.output.mkdir(parents=True, exist_ok=True)

    model = "estimates"
    model_dir = ""
    est_root: Path = args.estimates
    if args.estimates is None:
        from app.core.config import get_settings

        settings = get_settings()
        model = args.model or settings.ai_separation_model
        model_dir = str(Path(settings.ai_model_cache_dir) / "audio-separator")
        est_root = args.output / "estimates"

    rows: list[dict[str, Any]] = []
    seconds: dict[str, float] = {}
    for song_dir in songs:
        est_dir = est_root / song_dir.name
        if args.estimates is None:
            if not (song_dir / MIX).exists():
                print(f"{song_dir.name}: {MIX} 없음 — 건너뜀")
                continue
            seconds[song_dir.name] = round(separate(song_dir / MIX, est_dir, model, model_dir), 1)
        if not est_dir.is_dir():
            print(f"{song_dir.name}: 분리 결과 없음 — 건너뜀")
            continue
        check_rates(song_dir, est_dir)
        song_rows = score_song(song_dir.name, references(song_dir), estimates(est_dir))
        rows.extend(song_rows)
        print(song_dir.name, " ".join(f"{r['part']}={r['sdr']}" for r in song_rows))

    with (args.output / "scores.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["song", "part", "sdr", "si_sdr"])
        writer.writeheader()
        writer.writerows(rows)
    summary: dict[str, Any] = {
        "model": model,
        "songs": len({r["song"] for r in rows}),
        "seconds": seconds,
        "parts": summarize(rows),
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n{'악기':<8} {'곡':>3} {'SDR 중앙값':>10} {'SI-SDR 중앙값':>13}")
    for part, s in summary["parts"].items():
        print(f"{part:<8} {s['songs']:>3} {s['sdr_median']:>10} {s['si_sdr_median']:>13}")
    return summary


if __name__ == "__main__":
    main()
