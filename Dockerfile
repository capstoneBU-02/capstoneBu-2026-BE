# 빌드 타깃
# - api    : FastAPI 서버 (AI 패키지 없음)      docker build --target api .
# - worker : Celery 워커 (AI 패키지 포함)       docker build --target worker .
# 타깃을 지정하지 않으면 마지막 단계(worker)가 빌드된다. worker 이미지로도 api를 띄울 수 있다.

FROM python:3.12-slim AS base

# ffmpeg: MP4 오디오 추출·포맷 변환 (기능명세 IN-02)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 의존성 먼저 설치 (소스만 바뀌면 이 레이어는 캐시 재사용)
# uv 다운로드 캐시는 BuildKit 캐시 마운트에 두어 이미지에 남기지 않는다
COPY pyproject.toml uv.lock .python-version ./


FROM base AS api

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


FROM base AS worker

# libsndfile1: soundfile 오디오 입출력
RUN apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# gcc: diffq(audio-separator 의존성)는 Py3.12 리눅스 휠이 없어 소스 빌드 → 빌드 후 바로 제거
RUN --mount=type=cache,target=/root/.cache/uv \
    apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev \
    && uv sync --frozen --no-dev --group ai \
    && apt-get purge -y --auto-remove gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*

# 모델 가중치는 이미지에 넣지 않는다. /models는 compose의 model-cache 볼륨
# (scripts/download_models.py로 받음). 라이브러리 기본 캐시도 같은 볼륨을 쓰게 한다.
ENV AI_MODEL_CACHE_DIR=/models \
    TORCH_HOME=/models/torch \
    HF_HOME=/models/huggingface

COPY . .

CMD ["celery", "-A", "app.core.celery_app", "worker", "-Q", "cpu,gpu", "--loglevel=info", "--concurrency=1"]
