## 관련 이슈
- close #

## 작업 내용
-

## 확인 사항
- [ ] `uv run ruff check .` / `uv run ruff format .` / `uv run mypy app` / `uv run pytest` 통과
- [ ] 모델을 바꿨다면 마이그레이션 파일을 포함했다 (`alembic revision --autogenerate`)
- [ ] API 요청·응답 형태를 바꿨다면 하위 호환을 지켰다 (필드 추가 방식)
- [ ] (hotfix인 경우) `develop`에도 반영했다
