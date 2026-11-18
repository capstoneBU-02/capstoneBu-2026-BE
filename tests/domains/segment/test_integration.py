from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.input.models import Track
from app.domains.segment.models import Segment, SegmentHistory
from app.domains.segment.schemas import SegmentUpdateRequest
from app.domains.segment.service import update_segment


def test_segment_history_is_saved_in_postgres(db: Session) -> None:
    """이름과 범위 변경 시 실제 PostgreSQL에 이전 상태가 저장된다."""

    track = Track(
        workspace_id=10,
        filename="segment-test.mp3",
        media_type="audio/mpeg",
        storage_key="10/segment-test/original.mp3",
        file_hash="segment-test-hash",
        duration_ms=60000,
        file_size_bytes=1024,
        is_separated_stem=False,
    )
    db.add(track)
    db.flush()

    segment = Segment(
        track_id=track.id,
        start_ms=1000,
        end_ms=5000,
        name=None,
        version=1,
    )
    db.add(segment)
    db.commit()
    db.refresh(segment)

    segment_id = segment.id

    # 1. 이름만 변경: 버전은 유지되어야 한다.
    result = update_segment(
        db=db,
        segment_id=segment_id,
        request=SegmentUpdateRequest(name="VERSE"),
        track_duration_ms=60000,
    )

    assert result.name == "VERSE"
    assert result.version == 1

    history = (
        db.execute(select(SegmentHistory).where(SegmentHistory.segment_id == segment_id))
        .scalars()
        .all()
    )

    assert len(history) == 1
    assert history[0].start_ms == 1000
    assert history[0].end_ms == 5000
    assert history[0].name is None
    assert history[0].version == 1

    # 2. 범위 변경: 버전이 증가해야 한다.
    result = update_segment(
        db=db,
        segment_id=segment_id,
        request=SegmentUpdateRequest(
            start_ms=2000,
            end_ms=6000,
        ),
        track_duration_ms=60000,
    )

    assert result.start_ms == 2000
    assert result.end_ms == 6000
    assert result.name == "VERSE"
    assert result.version == 2

    # 3. 두 번째 변경 이전 상태도 별도 저장되어야 한다.
    history = (
        db.execute(
            select(SegmentHistory)
            .where(SegmentHistory.segment_id == segment_id)
            .order_by(SegmentHistory.id)
        )
        .scalars()
        .all()
    )

    assert len(history) == 2

    assert history[1].start_ms == 1000
    assert history[1].end_ms == 5000
    assert history[1].name == "VERSE"
    assert history[1].version == 1

    # 4. 현재 구간과 이력이 모두 DB에 존재하는지 확인한다.
    current = db.get(Segment, segment_id)

    assert current is not None
    assert current.start_ms == 2000
    assert current.end_ms == 6000
    assert current.name == "VERSE"
    assert current.version == 2
