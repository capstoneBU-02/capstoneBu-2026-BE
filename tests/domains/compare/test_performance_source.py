import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ErrorCode
from app.domains.compare import sources
from app.domains.input.models import Track
from app.domains.performance.models import Performance
from app.domains.segment.models import Segment


def create_performance(
    db: Session,
    workspace_id: int = 10,
) -> Performance:
    """실제 DB에 연주와 연결된 원곡·구간을 생성한다."""
    track = Track(
        workspace_id=workspace_id,
        filename="original.mp3",
        media_type="audio/mpeg",
        storage_key=f"{workspace_id}/tracks/original.mp3",
        file_hash="originalhash",
        duration_ms=300_000,
        file_size_bytes=1024,
        is_separated_stem=False,
    )
    db.add(track)
    db.flush()

    segment = Segment(
        track_id=track.id,
        start_ms=1000,
        end_ms=5000,
        name="VERSE",
        version=1,
    )
    db.add(segment)
    db.flush()

    performance = Performance(
        workspace_id=workspace_id,
        track_id=track.id,
        segment_id=segment.id,
        source="RECORDING",
        storage_key=f"{workspace_id}/performances/test.wav",
        file_hash="performancehash",
        start_ms=segment.start_ms,
        end_ms=segment.end_ms,
    )
    db.add(performance)
    db.flush()

    return performance


def test_load_performance_success(db: Session) -> None:
    """실제 REC 테이블에서 연주 정보를 정상 조회한다."""
    performance = create_performance(db)

    result = sources.load_performance(
        db,
        workspace_id=10,
        performance_id=performance.id,
    )

    assert result.performance_id == performance.id
    assert result.workspace_id == 10
    assert result.track_id == performance.track_id
    assert result.segment_id == performance.segment_id
    assert result.start_ms == 1000
    assert result.end_ms == 5000
    assert result.storage_key == performance.storage_key
    assert result.file_hash == performance.file_hash


def test_load_performance_not_found(db: Session) -> None:
    """존재하지 않는 연주 ID는 PERFORMANCE_NOT_FOUND를 반환한다."""
    with pytest.raises(BusinessException) as exc_info:
        sources.load_performance(
            db,
            workspace_id=10,
            performance_id=999999,
        )

    assert exc_info.value.error_code == ErrorCode.PERFORMANCE_NOT_FOUND


def test_load_performance_other_workspace_forbidden(db: Session) -> None:
    """다른 워크스페이스의 연주는 조회할 수 없다."""
    performance = create_performance(db, workspace_id=10)

    with pytest.raises(BusinessException) as exc_info:
        sources.load_performance(
            db,
            workspace_id=99,
            performance_id=performance.id,
        )

    assert exc_info.value.error_code == ErrorCode.PERFORMANCE_NOT_FOUND
