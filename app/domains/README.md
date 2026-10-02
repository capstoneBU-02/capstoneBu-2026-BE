# 도메인 폴더 규칙

기능(도메인) 단위로 폴더를 나눕니다. 새 도메인은 아래 구조를 그대로 복사해서 시작하세요.

```
app/domains/songs/
├── __init__.py
├── router.py     # Spring Controller — 요청/응답 변환, 검증, 상태 코드만. 비즈니스 로직 금지
├── service.py    # Spring Service — 비즈니스 로직, 트랜잭션(db.commit)
├── models.py     # Spring Entity — SQLAlchemy 모델
├── schemas.py    # Spring DTO — Pydantic 요청/응답 모델
└── tasks.py      # (필요할 때) Celery 비동기 작업
```

도메인 전용 코드는 전부 이 폴더 안에 둡니다. 여러 도메인이 함께 쓰는 것만 `app/core/`로 올립니다.

## 등록 체크리스트
1. `router.py` 작성 후 `app/main.py`의 `include_router`에 추가
2. 마이그레이션 생성: `uv run alembic revision --autogenerate -m "add songs"`
   (`models.py`, `tasks.py`는 자동으로 인식되므로 따로 등록할 필요 없음)
3. 엔티티는 `app/core/base_entity.py`의 `Base`, `TimestampMixin`, `SoftDeleteMixin`을 상속
4. 에러 코드는 `app/core/exceptions.py`의 `ErrorCode`에 도메인 주석과 함께 추가

## 예시
```python
# schemas.py
class SongCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)

class SongResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str

# router.py
router = APIRouter(prefix="/songs", tags=["songs"])

@router.post("", status_code=201, response_model=ApiResponse[SongResponse])
def create_song(body: SongCreate, db: Session = Depends(get_db)) -> ApiResponse[SongResponse]:
    song = service.create_song(db, body)
    return ApiResponse.ok(SongResponse.model_validate(song))

# service.py
def get_song(db: Session, song_id: int) -> Song:
    song = db.get(Song, song_id)
    if song is None or song.deleted_at is not None:
        raise BusinessException(ErrorCode.RESOURCE_NOT_FOUND)
    return song
```

## 소유 단위
PoC 1차는 로그인 대신 **테스터 코드 → 워크스페이스**로 사용자를 식별합니다 (기능명세 ACC-06·07, D24).
모든 데이터 테이블에 `workspace_id`를 두고, S3 key도 `{workspace_id}/...`로 시작합니다.
