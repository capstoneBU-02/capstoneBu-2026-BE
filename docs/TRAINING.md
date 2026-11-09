# Basic Pitch 학습 안내

Kaggle 무료 GPU에서 Basic Pitch를 미세조정하고, 기존 모델과 비교하는 방법입니다.
학습 코드는 `scripts/train/`에 있고 서비스 이미지에서는 사용하지 않습니다.

서비스는 공개 가중치로 먼저 동작합니다. 학습 모델은 같은 평가 곡에서 더 좋을 때만 교체합니다.

## 1. 데이터 준비

음원과 정답 MIDI, `manifest.csv`를 한 폴더에 둡니다.

```text
data/
├── manifest.csv
├── audio/song1_verse.wav
└── midi/song1_verse.mid
```

```csv
track_id,song,split,kind,audio,midi
song1_verse,song1,train,electric,audio/song1_verse.wav,midi/song1_verse.mid
song2_riff,song2,validation,electric,audio/song2_riff.wav,midi/song2_riff.mid
song3_solo,song3,test,electric,audio/song3_solo.wav,midi/song3_solo.mid
```

| 열 | 내용 |
|---|---|
| `track_id` | 음원마다 다른 이름. 영문, 숫자, `.`, `_`, `-`만 사용 |
| `song` | 곡 이름. 같은 곡의 구간은 같은 값 |
| `split` | `train`, `validation`, `test` |
| `kind` | `electric`, `acoustic` |
| `audio`, `midi` | `manifest.csv` 기준 상대 경로 |

지켜야 할 규칙은 다음과 같습니다.

- 음원은 기타 단독이고 3초 이상이어야 합니다.
- MIDI에는 음마다 정확한 시작, 끝, 음높이가 있어야 합니다.
- 같은 곡을 여러 split에 넣지 않습니다. `manifest.csv` 검사에서 오류가 납니다.
- `test` 곡은 학습에 쓰지 않습니다. 평가용 곡은 처음에 정해 두고 바꾸지 않습니다.
- 사용 권한이 있는 음원만 쓰고, 원곡 음원은 넣지 않습니다.
- 벤딩은 정답에 반영되지 않습니다. 음높이 곡선(contour)은 각 음의 중심 음높이로 근사합니다.

## 2. Kaggle 준비

1. Kaggle에서 전화번호를 인증합니다.
2. **Create → New Dataset**으로 `data` 폴더를 **Private**으로 올립니다.
3. `scripts/train/kaggle_training.ipynb`를 Kaggle에 불러옵니다(**File → Import Notebook**).
4. **Settings**에서 **GPU T4 x2**와 **Internet On**을 선택합니다.
5. **Add Input**으로 올린 Dataset을 연결합니다.

## 3. 점검 실행

처음에는 노트북의 `SMOKE_TEST = True`로 끝까지 실행합니다. 가짜 데이터로 설치부터 비교까지 확인하는 단계이며, 이때 나오는 F1과 가중치는 쓰지 않습니다.

## 4. 실제 학습

1. 첫 번째 코드 셀을 수정합니다.

   ```python
   SMOKE_TEST = False
   MANIFEST = "/kaggle/input/<dataset-name>/manifest.csv"
   ```

2. **Save Version → Save & Run All**로 실행합니다. 브라우저를 닫아도 계속됩니다.
3. 끝나면 Output의 `run` 폴더를 내려받습니다.

| 파일 | 내용 |
|---|---|
| `best.weights.h5` | 검증 손실이 가장 낮은 가중치 |
| `last.weights.h5` | 마지막 가중치. 이어서 학습할 때 사용 |
| `state.json` | 완료한 에폭과 최저 검증 손실 |
| `history.csv` | 에폭별 손실 |
| `model.onnx` | 서비스 형식으로 변환한 모델 |
| `evaluation.csv` | 곡별 F1 비교 |

## 5. 이어서 학습

Kaggle은 세션 시간과 주간 GPU 사용량에 제한이 있습니다. 끊겼다면 이전 실행의 Output을 Dataset이나 Notebook Output으로 입력에 연결하고 설정을 바꿉니다.

```python
RESUME_FROM = "/kaggle/input/<이전 결과 폴더>/run"
```

옵티마이저 상태는 저장하지 않아 이어서 학습할 때 새로 시작합니다.

오래 돌린다고 계속 좋아지지 않습니다. 검증 손실이 `PATIENCE`번 연속 개선되지 않으면 자동으로 멈춥니다. 더 올리려면 데이터를 늘리는 것이 좋습니다.

## 6. 결과 판단

노트북 마지막 출력을 확인합니다. 지표는 시작 ±50ms, 음높이 일치 기준 노트 F1입니다.

```text
electric 평균 F1: 기존 0.6000, 학습 0.7000
acoustic 평균 F1: 기존 0.8000, 학습 0.8100
판정: 교체 가능
```

위 숫자는 예시입니다. `교체 가능`은 일렉 평균 F1이 오르고 어쿠스틱 평균 F1이 떨어지지 않았다는 뜻입니다. 곡 수가 적으면 우연일 수 있으니 곡별 결과도 함께 봅니다.

## 7. 서비스 교체

평가를 통과했을 때 별도 이슈와 PR로 진행합니다.

1. `model.onnx`를 팀이 정한 위치에 보관합니다. 가중치 파일은 Git에 올리지 않습니다.
2. `AI_TRANSCRIPTION_WEIGHTS`를 `ft-YYYYMMDD`로, `AI_CONFIG_VERSION`을 새 값으로 바꿉니다.
3. PR에 이전·이후 지표 표를 붙입니다.
4. 사용한 데이터의 출처와 권리를 `docs/MODELS.md`에 기록합니다.
