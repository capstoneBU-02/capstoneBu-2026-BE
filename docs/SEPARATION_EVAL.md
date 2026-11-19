# 음원 분리 평가 안내

분리 모델(지금은 BS-Roformer-SW)이 악기를 얼마나 잘 나누는지 점수로 잽니다.
학습 전에 기준 점수를 남기고, 학습한 모델도 같은 곡으로 다시 재서 비교합니다.
코드는 `scripts/separation/evaluate.py`이고 서비스에서는 사용하지 않습니다.

## 1. 평가 곡 준비

곡마다 폴더를 만들고, 섞인 음원 `mix.wav`와 악기별 정답 파일을 넣습니다.

```text
scripts/separation/data/        # git에 올라가지 않음
├── song1/mix.wav
├── song1/guitar.wav
├── song1/bass.wav
└── song2/...
```

- 악기 파일 이름: `vocals`, `drums`, `bass`, `guitar`, `piano`, `other` (`.wav`)
- 없는 악기는 빼도 됩니다. 그 악기만 채점하지 않습니다.
- 한 곡 안의 파일은 샘플레이트가 같아야 합니다 (44.1kHz 권장).
- 정답 파일은 원곡의 멀티트랙(악기별 원본)이어야 합니다. 분리 결과를 정답으로 쓰면 안 됩니다.
- 평가 곡은 처음에 정해 두고 바꾸지 않습니다. 학습 데이터에도 넣지 않습니다.

## 2. 실행

worker 컨테이너에서 지금 설정의 모델로 분리하고 채점합니다.

```bash
docker compose run --rm worker python scripts/separation/evaluate.py \
    --data scripts/separation/data --output scripts/separation/runs/base
```

- `--model`로 다른 분리 모델을 지정할 수 있습니다 (기본: `AI_SEPARATION_MODEL`).
- 학습한 모델처럼 이미 분리한 결과가 있으면 `--estimates 폴더`로 채점만 합니다.
  폴더 구조는 평가 곡 폴더와 같습니다 (`곡이름/악기.wav`).

## 3. 결과 읽기

| 파일 | 내용 |
|---|---|
| `scores.csv` | 곡·악기별 SDR, SI-SDR |
| `summary.json` | 악기별 곡 수, SDR 중앙값·평균, SI-SDR 중앙값, 곡별 분리 시간 |
| `estimates/` | 분리 결과 (다시 들어 볼 때) |

- **SDR**(dB): 정답과 얼마나 가까운지. 클수록 좋습니다.
- **SI-SDR**(dB): 소리 크기 차이는 빼고 모양만 비교합니다.
- **backing**: 서비스의 반주처럼 기타를 뺀 분리 결과를 합친 것입니다. 정답은 `mix - guitar`입니다.
- 정답이 거의 무음(-60dBFS 이하)인 악기는 점수가 의미 없으므로 빈칸으로 남깁니다.
- 곡마다 차이가 커서 평균보다 중앙값으로 비교합니다.

## 4. 학습 모델 교체 기준

같은 평가 곡에서 기타와 반주의 SDR 중앙값이 기존 모델보다 높을 때만 교체합니다.
교체하면 `AI_SEPARATION_MODEL`과 `ai_config_version`을 함께 바꿉니다.
