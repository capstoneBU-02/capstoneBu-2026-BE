# AI 모델·패키지 라이선스

worker 이미지(`--target worker`)에 들어가는 AI 모델과 패키지의 라이선스입니다.
새 모델·패키지를 추가하면 이 표에 함께 적어 주세요.

| 구성 | 라이선스 | 비고 |
|---|---|---|
| BS-Roformer-SW 가중치 | CC BY-NC-SA 4.0 | 비상업 전용. MVP에만 사용, 상용화 전 교체 |
| htdemucs_6s 가중치 | 명시 없음 | 상용 대안으로 쓰지 않음 |
| diffq (audio-separator 의존성) | CC BY-NC 4.0 | 상용 이미지에서 제거 필요 |
| basic-pitch | Apache 2.0 | |
| beat-this (코드·가중치) | MIT | |
| torch / onnxruntime | BSD 계열 / MIT | |
| librosa, resampy / soundfile / music21 | ISC / BSD-3 / BSD-3 | |
| soxr, libsndfile | LGPL 2.1+ | 서버 실행만, 배포 시 고지 |
| ffmpeg (apt) | GPL 빌드 | GHCR 이미지 비공개 유지 |

## 가중치 관리

- 가중치는 이미지에 넣지 않습니다. `scripts/download_models.py`로 `AI_MODEL_CACHE_DIR`(기본 `/models`)에 받습니다.
- 사용할 모델은 `AI_SEPARATION_MODEL`, `AI_BEAT_MODEL` 설정을 따릅니다. 바꾸면 `AI_CONFIG_VERSION`도 올립니다.
