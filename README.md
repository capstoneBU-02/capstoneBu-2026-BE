# capstone-backend

음원 기반 악기 분리·타브 생성 및 카피 보조 웹서비스의 백엔드입니다.

## 기술 스택

| 항목 | 선택 |
|---|---|
| 언어 | Python 3.12 |
| 패키지 관리 | uv |
| API | FastAPI + Pydantic 2 |
| DB | PostgreSQL 17 + SQLAlchemy 2.0(동기) + Alembic |
| 작업 큐 | Celery 5 + Redis 7 |
| 파일 저장소 | SeaweedFS (S3 호환, 배포 시 AWS S3로 교체 가능) |
| AI 모델 | 기타 분리 BS-Roformer-SW (audio-separator) · 음 인식 basic-pitch (ONNX) · 박 추정 Beat This! |
| AI 실행 | Celery worker 전용 이미지 · PyTorch CPU · 가중치는 `model-cache` 볼륨 (라이선스: [`docs/MODELS.md`](docs/MODELS.md)) |
| 품질 | ruff · mypy · pytest |

## 처음 세팅 (Windows 기준)

### 1. 필수 설치
- **Docker Desktop**: https://www.docker.com/products/docker-desktop
- **uv** (PowerShell):
  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```
- **Git**

### 2. 의존성 설치
```bash
uv python install 3.12   # Python 3.12 자동 설치 (이미 있으면 생략됨)
uv sync                  # .venv 생성 + 패키지 설치 (uv.lock 버전 그대로)
uv sync --group ai       # AI 코드를 로컬에서 작성·실행할 때만 (PyTorch 등, 수 GB)
```

API·CI는 `uv sync`(AI 패키지 없음)만으로 동작합니다. AI 패키지는 worker 이미지에만 들어갑니다.

### 3. 환경 변수
노션 **DB·IP** 페이지에서 `.env` 파일을 받아 **레포 최상위 폴더**(`pyproject.toml`과 같은 위치)에 둡니다.
`.env`는 git에 올리지 않습니다. 설정값을 추가·변경하면 노션의 `.env`도 함께 갱신해 주세요.

AI 설정은 `AI_*` 환경 변수로 덮어쓸 수 있고, 없으면 `app/core/config.py`의 기본값을 씁니다.
```dotenv
AI_MODEL_CACHE_DIR=/models
AI_SEPARATION_MODEL=BS-Roformer-SW.ckpt
AI_CONFIG_VERSION=2026-10-01
```

### 4. 전체 실행
```bash
docker compose up --build
```

| 주소 | 내용 |
|---|---|
| http://localhost:8000/docs | Swagger (API 문서·테스트) |
| http://localhost:8000/api/v1/health | 서버 상태 |
| http://localhost:8000/api/v1/health/ready | DB·Redis·저장소 연결 상태 |
| http://localhost:9333 | SeaweedFS 관리 화면 |

Celery 모니터링(Flower)까지 띄우려면: `docker compose --profile monitor up` → http://localhost:5555

이미지는 빌드 타깃 두 개로 나뉩니다: `api`(AI 패키지 없음) / `worker`(AI 패키지 포함). 코드는 같고 실행 명령만 다릅니다.

### 5. AI 모델 다운로드 (처음 한 번)
```bash
docker compose run --rm worker python scripts/download_models.py
```
가중치는 이미지에 넣지 않고 `model-cache` 볼륨(`/models`)에 받습니다. 이미 있으면 건너뛰고, 파일별 SHA-256을 출력합니다.

> **worker는 코드 자동 반영(--reload)이 없습니다.** `app/` 코드를 고친 뒤에는 `docker compose restart worker`로 다시 띄워 주세요.

## 자주 쓰는 명령어

```bash
uv add <패키지>                 # 의존성 추가
uv add --dev <패키지>           # 개발용 의존성 추가

uv run ruff check .             # 린트
uv run ruff format .            # 자동 포맷
uv run mypy app                 # 타입 체크
uv run pytest                   # 테스트

# DB 마이그레이션 (컨테이너 안에서 실행)
docker compose exec api alembic revision --autogenerate -m "add songs"
docker compose exec api alembic upgrade head
```

## 폴더 구조

**도메인(기능)별**로 나눕니다. 공통 코드는 `core/`에만, 기능 코드는 `domains/<기능>/` 안에만 둡니다.

```
capstone-backend/
├── app/
│   ├── main.py              # 앱 생성, 라우터 등록 (Spring의 Application 클래스)
│   ├── core/                # 공통 (Spring의 global 패키지)
│   │   ├── config.py        #   설정 (.env 읽기)
│   │   ├── database.py      #   DB 연결·세션
│   │   ├── base_entity.py   #   엔티티 부모 클래스 (생성일·수정일·소프트 삭제)
│   │   ├── exceptions.py    #   ErrorCode, BusinessException, 예외 핸들러
│   │   ├── response.py      #   공통 응답 ApiResponse
│   │   ├── middleware.py    #   trace_id
│   │   ├── storage.py       #   파일 저장소 (S3 / presigned URL)
│   │   └── celery_app.py    #   비동기 작업 큐 설정
│   └── domains/             # 기능별 폴더 ← 여기에 기능 추가
│       └── health/          #   예) router.py · schemas.py · models.py · service.py · tasks.py
├── tests/                   # app/ 과 같은 구조 (tests/core, tests/domains/<기능>)
├── alembic/                 # DB 마이그레이션
├── scripts/                 # 운영 스크립트 (모델 다운로드 등)
├── docs/                    # 문서 (모델 라이선스 등)
├── docker/                  # 컨테이너 설정 파일
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml           # 의존성·도구 설정 (Spring의 build.gradle)
```

새 기능을 추가하는 방법은 [`app/domains/README.md`](app/domains/README.md)를 보세요.

## AI 파이프라인

AI는 **음원 → 타브 초안 생성**(PoC 1차)과 **사용자 연주 ↔ 원곡 비교**(PoC 2차)에 쓰입니다.
사전 학습 모델 3개(기타 분리·음 인식·박 추정)를 쓰고, 타브 운지와 결과 판정은 직접 설계한 알고리즘으로 처리합니다.
AI 결과는 모두 사용자가 원음과 함께 확인하는 **초안**이며, 생성 성공을 카피 정답으로 표시하지 않습니다(COPY-04).

| 단계 | 하는 일 | 기술 | 종류 | 범위 |
|---|---|---|---|---|
| 기타 분리 | 원곡 구간에서 기타 소리만 추출 | BS-RoFormer-SW (대체 Demucs htdemucs_6s, `audio-separator`) | 사전 학습 | 1차 |
| 음 인식 | 음높이·시작·길이·벤딩 인식 | Basic Pitch (ONNX, CPU) | 사전 학습 | 1·2차 |
| 박 추정 | 박·마디 첫 박 위치 | Beat This! `final0` (대체 librosa `beat_track`) | 사전 학습 | 1차 |
| 타브 변환 | 음마다 줄·프렛 선택 | Viterbi 운지 최적화 + 연주 가능성 검사 | 직접 설계 | 1차 |
| 결과 판정 | 빈 결과·낮은 신뢰도를 성공으로 표시하지 않음 | 임계값 규칙 | 직접 설계 | 1·2차 |
| 연주 비교 | 사용자 연주와 원곡의 차이 의심 구간 | DTW 정렬 + 노트 비교 | 알고리즘 | 2차 |

### 흐름

```
업로드 ─ preprocess ─ separate ─ transcribe ─ quantize ─ build_tab ─ MusicXML → alphaTab(프론트)
          (cpu)        (gpu)       (cpu)        (cpu)       (cpu)
분리 음원 업로드(JOB-02)는 separate를 건너뛰고 구간 WAV를 guitar.wav로 복사
```

| 태스크 | 입력 | 출력 (저장소 key, 앞에 `{workspace_id}/{track_id}/`) | 큐 |
|---|---|---|---|
| `processing.preprocess(stem_id)` | 원본 파일, `start_ms`·`end_ms` | `segments/{segment_id}/{stem_id}/input.wav` (앞뒤 0.5초 여유) | cpu |
| `processing.separate(stem_id)` | 구간 WAV | `stems/{segment_id}/{stem_id}/guitar.wav` (여유 제거) | gpu |
| `transcribe(tab_id)` | 기타 음원 | `tabs/{tab_id}/notes.json` | cpu |
| `quantize(tab_id)` | 노트, 구간 WAV | `tabs/{tab_id}/rhythm.json` | cpu |
| `build_tab(tab_id)` | 정량화 노트, 연주 조건 | `tabs/{tab_id}/tab.json`, `tab.musicxml` | cpu |

- 단계끼리는 함수를 직접 부르지 않고 **저장소 파일 + 스키마**로만 주고받습니다. 그래서 단계별로 따로 개발·테스트·재시도할 수 있습니다.
- 각 태스크는 끝날 때 결과(`success` / `needs_review` / `failed`, `error_code`, `model`)를 도메인별 테이블(`stem_stage_results`, `tab_stage_results`)에 남깁니다.
- 재시도는 실패한 태스크부터 앞 단계 결과를 재사용해 최대 3회. 기타 음원 해시 + 결과 유형 + 연주 조건 + `config_version`이 같으면 기존 타브를 돌려줍니다.

### 코드 위치

```
app/domains/
├── processing/   # 분리·처리 (JOB)  tasks.py: preprocess, separate      pipeline/: preprocess.py, separation.py
├── tab/          # 타브·악보 (TAB)  tasks.py: transcribe, quantize, build_tab
│                 #                  pipeline/: transcription.py, rhythm.py, fingering.py, playability.py, musicxml.py
└── compare/      # 연주 비교 (CMP, 2차)  pipeline/: align.py, notes.py
tests/domains/<기능>/   # 단위 테스트 (짧은 픽스처 음원, 모델 추론은 mock)
scripts/eval/           # 모델 평가 스크립트 (무거워서 CI 밖에서 실행)
```

### 큐·워커

| 큐 | 처리 | 워커 |
|---|---|---|
| `cpu` (기본) | 전처리, 음 인식, 리듬, 타브 | `worker` |
| `gpu` | 기타 분리 | `worker`가 CPU로 함께 처리 (`-Q cpu,gpu`) |

worker 이미지는 **CPU 전용**입니다(PyTorch CPU 빌드). GPU 서버용 워커는 GPU 의존성을 따로 구성할 때 추가합니다.
GPU 태스크는 `@celery_app.task(queue="gpu")`로 지정합니다. 작업 상태는 Flower(`--profile monitor`)에서 봅니다.

### AI 설정 (`app/core/config.py`, env `AI_*`)

| 설정 | 기본값 | 설명 |
|---|---|---|
| `AI_CONFIG_VERSION` | `2026-10-01` | 모든 결과에 저장. 모델·임계값을 바꾸면 함께 올림 (버전이 다르면 캐시 안 씀) |
| `AI_SEPARATION_MODEL` | `BS-Roformer-SW.ckpt` | 기타 분리 모델 |
| `AI_SEPARATION_FALLBACK` | `htdemucs_6s` | 분리 대체 모델 |
| `AI_TRANSCRIPTION_WEIGHTS` | `icassp2022-onnx` | Basic Pitch 가중치 (미세조정 시 `ft-YYYYMMDD`) |
| `AI_ONSET_THRESHOLD` / `AI_FRAME_THRESHOLD` | `0.5` / `0.3` | Basic Pitch 추론 임계값 |
| `AI_BEAT_MODEL` | `final0` | Beat This! 체크포인트 |
| `AI_TAB_WEIGHTS` | `{"span":1.0,"pos":0.1,"move":0.5,"strskip":0.3}` | Viterbi 비용 가중치 (JSON) |
| `AI_LOW_CONFIDENCE` | `0.4` | 이 값 미만 음은 `low_confidence`로 표시 (지우지 않음) |
| `AI_SEPARATION_RMS_DB` | `30` | 기타 스템이 입력보다 이만큼 작으면 분리 실패 |
| `AI_MODEL_CACHE_DIR` | `/models` | 가중치 캐시 (`model-cache` 볼륨) |

임계값은 초기값입니다. 평가 데이터로 조정한 뒤 반영하고, 바꾸면 노션 `.env`도 함께 갱신합니다.

### 상태 판정

| 단계 | 조건 | 상태 | ErrorCode 예 |
|---|---|---|---|
| 전처리 | 무음(-50dBFS 미만)·디코딩 실패·길이 0초 | failed (재시도 불가) | `PREPROCESS_INVALID_AUDIO` |
| 기타 분리 | 기타 스템이 입력보다 30dB 이상 작음 | failed (재시도 불가) | `SEPARATION_NO_GUITAR` |
| 기타 분리 | 유효 음 구간 10% 미만 | needs_review | `SEPARATION_UNCLEAR` |
| 음 인식 | 인식된 음 0개 | failed | `TAB_NO_NOTES` |
| 음 인식 | low_confidence 음이 50% 이상 | needs_review | `TAB_LOW_CONFIDENCE` |
| 리듬 | 4/4가 아닌 박자로 추정 | needs_review | |
| 타브 | `playable=False` 노트 있음 | needs_review | |
| 공통 | 시간 초과·워커 오류 | failed | `AI_WORKER_TIMEOUT` |

확신이 없는 구간은 틀렸다고 표시하지 않고 **판단 보류**로 둡니다.

### 평가

모델·설정을 바꾸는 PR은 `scripts/eval/`을 로컬에서 돌려 이전·이후 지표 표를 PR에 붙입니다.
지표: 기타 SDR, 노트 F1(±50ms, `mir_eval`), 박 F-measure(±70ms), 운지 일치율, 연주 불가 노트 비율, 구간 30초당 처리 시간.
CI의 pytest는 판정 규칙과 파이프라인 동작만 검사하고 모델 추론은 mock으로 대체합니다.

### AI 코드 작성 규칙

- **AI 라이브러리는 모듈 최상단에서 import하지 않습니다.** (torch, basic_pitch, beat_this, audio_separator, librosa, music21 등)
  함수 안에서 지연 import 하세요. Celery가 모든 도메인의 `tasks.py`를 자동 import하고, api 이미지와 CI에는 AI 패키지가 없습니다.
  ```python
  def separate(path: str) -> str:
      from audio_separator.separator import Separator  # 함수 안에서
      ...
  ```
- **모델 이름·임계값은 하드코딩하지 않고** `get_settings().ai_*`에서 읽습니다. 값을 바꾸면 `AI_CONFIG_VERSION`도 함께 올립니다.
- 모델 사전 로드는 `app/core/celery_app.py`의 `preload_ai_models` 훅에 연결합니다.
- 공통 코드는 `core/`, 기능 코드는 `domains/<기능>/`에 둡니다. 파일 저장 key는 `{workspace_id}/...`로 시작하고(위 태스크 표), 읽기·쓰기는 `core/storage.py`만 거칩니다.
- 실패는 `ErrorCode`의 AI 파이프라인 코드(`SEPARATION_NO_GUITAR`, `TAB_LOW_CONFIDENCE` 등)로 표현합니다.
- 새 모델·패키지를 추가하면 [`docs/MODELS.md`](docs/MODELS.md)에 라이선스를 적습니다.

## 공통 응답 형식

```json
// 성공
{ "success": true, "data": { ... } }

// 실패
{ "success": false, "error": { "code": "RESOURCE_NOT_FOUND", "message": "요청한 리소스를 찾을 수 없습니다." }, "meta": { "trace_id": "..." } }
```

- Service에서 `raise BusinessException(ErrorCode.XXX)` → 자동으로 위 형식으로 변환
- 에러 코드는 `app/core/exceptions.py`의 `ErrorCode`에서 중앙 관리

## 브랜치 · CI/CD

```
main      ── 프로덕션. 직접 push 금지, develop에서 승격
develop   ── 개발 서버. 기능 브랜치의 PR 대상
  ├─ feat/#12-workspace-session   (fix / refactor / chore / docs / test / ci 동일)
hotfix/#34-xxx  ── main에서 분기 → main 머지 후 develop에도 반영
```

| 워크플로 | 언제 | 하는 일 |
|---|---|---|
| `ci.yml` | develop·main으로 PR, push | ruff · mypy · 마이그레이션(upgrade → check → downgrade → upgrade) · pytest · 도커 빌드 |
| `cd.yml` | develop·main push | GHCR에 `api`·`worker` 이미지를 각각 push (`sha-<커밋>` + `develop`/`latest`). 서버 배포 단계는 배포 대상 확정 후 활성화 |

- **CI가 실패한 PR은 머지하지 않습니다.** PR 올리기 전에 로컬에서 `ruff` · `mypy` · `pytest`를 먼저 돌려 주세요.
- 모델을 바꾸고 마이그레이션 파일을 빼먹으면 CI의 `alembic check`에서 실패합니다.
- 롤백: 서버에서 이전 `sha-<커밋>` 이미지로 `IMAGE_TAG`를 바꿔 다시 띄웁니다.

## IDE 설정

**VS Code** (추천)
- 확장: `Python`, `Pylance`, `Ruff`, `Docker`
- `Ctrl+Shift+P` → *Python: Select Interpreter* → `.venv` 선택

**PyCharm / IntelliJ IDEA**
- IntelliJ는 *Python* 플러그인 설치 필요 (Ultimate 권장)
- *Settings → Project → Python Interpreter* → *Add Interpreter → Existing* → `.venv\Scripts\python.exe`
- *Ruff* 플러그인 설치 권장
