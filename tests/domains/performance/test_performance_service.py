from io import BytesIO
from typing import Any

import pytest
from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import Headers

from app.core.exceptions import BusinessException, ErrorCode
from app.domains.input.models import Track
from app.domains.performance import service
from app.domains.performance.models import Performance, PerformanceHistory
from app.domains.segment.models import Segment


def create_segment(
    db: Session,
    workspace_id: int = 10,
) -> Segment:
    """테스트용 원곡과 연습 구간을 생성한다."""
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

    return segment


def create_upload(
    filename: str = "performance.wav",
    content: bytes = b"test audio data",
    content_type: str | None = None,
) -> UploadFile:
    """테스트용 오디오 업로드 파일을 생성한다."""
    headers = Headers({"content-type": content_type}) if content_type else None

    return UploadFile(
        file=BytesIO(content),
        filename=filename,
        headers=headers,
    )


def mock_storage(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """외부 Object Storage 호출을 대체한다."""
    uploaded: list[dict[str, Any]] = []

    def fake_put_bytes(
        key: str,
        data: bytes,
        content_type: str,
    ) -> None:
        uploaded.append(
            {
                "key": key,
                "data": data,
                "content_type": content_type,
            }
        )

    monkeypatch.setattr("app.core.storage.put_bytes", fake_put_bytes)
    return uploaded


def test_create_performance_success(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """연주 파일 저장 및 DB 등록 성공."""
    segment = create_segment(db)
    uploaded = mock_storage(monkeypatch)

    result = service.create_performance(
        db=db,
        workspace_id=10,
        segment_id=segment.id,
        source="RECORDING",
        audio_file=create_upload(),
    )

    performance = db.get(Performance, result.performance_id)

    assert performance is not None
    assert result.workspace_id == 10
    assert result.track_id == segment.track_id
    assert result.segment_id == segment.id
    assert result.source == "RECORDING"
    assert result.start_ms == 1000
    assert result.end_ms == 5000
    assert len(uploaded) == 1
    assert uploaded[0]["data"] == b"test audio data"
    assert uploaded[0]["content_type"] == "audio/wav"


def test_create_performance_auto_history(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """같은 구간에 새 연주를 등록하면 이전 연주와 자동 연결한다."""
    segment = create_segment(db)
    mock_storage(monkeypatch)

    first = service.create_performance(
        db=db,
        workspace_id=10,
        segment_id=segment.id,
        source="RECORDING",
        audio_file=create_upload(),
    )

    second = service.create_performance(
        db=db,
        workspace_id=10,
        segment_id=segment.id,
        source="RECORDING",
        audio_file=create_upload(content=b"second audio"),
    )

    assert first.performance_id != second.performance_id

    history = db.scalar(
        select(PerformanceHistory).where(PerformanceHistory.performance_id == second.performance_id)
    )

    assert history is not None
    assert history.previous_performance_id == first.performance_id


def test_create_performance_preserves_segment_snapshot(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """연주 등록 당시의 구간 범위를 스냅샷으로 보존한다."""
    segment = create_segment(db)
    mock_storage(monkeypatch)

    first = service.create_performance(
        db=db,
        workspace_id=10,
        segment_id=segment.id,
        source="RECORDING",
        audio_file=create_upload(),
    )

    segment.start_ms = 2000
    segment.end_ms = 6000
    db.commit()

    second = service.create_performance(
        db=db,
        workspace_id=10,
        segment_id=segment.id,
        source="RECORDING",
        audio_file=create_upload(content=b"updated audio"),
    )

    first_row = db.get(Performance, first.performance_id)
    second_row = db.get(Performance, second.performance_id)

    assert first_row is not None
    assert second_row is not None

    assert (first_row.start_ms, first_row.end_ms) == (1000, 5000)
    assert (second_row.start_ms, second_row.end_ms) == (2000, 6000)


def test_get_performances_by_segment_latest_first(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """특정 구간의 연주 목록을 최신순으로 조회한다."""
    segment = create_segment(db)
    mock_storage(monkeypatch)

    first = service.create_performance(
        db=db,
        workspace_id=10,
        segment_id=segment.id,
        source="RECORDING",
        audio_file=create_upload(),
    )

    second = service.create_performance(
        db=db,
        workspace_id=10,
        segment_id=segment.id,
        source="UPLOAD",
        audio_file=create_upload(content=b"second audio"),
    )

    result = service.get_performances_by_segment(
        db=db,
        workspace_id=10,
        segment_id=segment.id,
    )

    performance_ids = [item.performance_id for item in result.performances]

    assert result.segment_id == segment.id
    assert performance_ids == [
        second.performance_id,
        first.performance_id,
    ]


def test_create_performance_other_workspace_forbidden(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """다른 워크스페이스의 구간에 연주를 등록할 수 없다."""
    segment = create_segment(db, workspace_id=10)
    uploaded = mock_storage(monkeypatch)

    with pytest.raises(BusinessException) as exc_info:
        service.create_performance(
            db=db,
            workspace_id=99,
            segment_id=segment.id,
            source="RECORDING",
            audio_file=create_upload(),
        )

    assert exc_info.value.error_code == ErrorCode.SEGMENT_NOT_FOUND
    assert uploaded == []


@pytest.mark.parametrize(
    ("filename", "content", "source"),
    [
        ("performance.wav", b"", "RECORDING"),
        ("performance.txt", b"test audio", "RECORDING"),
        ("performance.wav", b"test audio", "INVALID"),
    ],
)
def test_create_performance_invalid_file(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    content: bytes,
    source: str,
) -> None:
    """빈 파일, 미지원 확장자, 잘못된 입력 방식을 거부한다."""
    segment = create_segment(db)
    uploaded = mock_storage(monkeypatch)

    with pytest.raises(BusinessException) as exc_info:
        service.create_performance(
            db=db,
            workspace_id=10,
            segment_id=segment.id,
            source=source,
            audio_file=create_upload(filename, content),
        )

    assert exc_info.value.error_code == ErrorCode.INVALID_INPUT
    assert uploaded == []


def test_create_performance_accepts_webm_opus(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """브라우저 녹음의 WebM Opus MIME 타입을 허용한다."""
    segment = create_segment(db)
    uploaded = mock_storage(monkeypatch)

    result = service.create_performance(
        db=db,
        workspace_id=10,
        segment_id=segment.id,
        source="RECORDING",
        audio_file=create_upload(
            filename="blob",
            content_type="audio/webm;codecs=opus",
        ),
    )

    assert result.performance_id > 0
    assert len(uploaded) == 1
    assert uploaded[0]["content_type"] == "audio/webm"


def test_validate_audio_file_accepts_webm_opus() -> None:
    """브라우저 녹음의 WebM Opus MIME 타입을 허용한다."""
    content = b"test audio data"

    audio_file = create_upload(
        filename="blob",
        content=content,
        content_type="audio/webm;codecs=opus",
    )

    result = service.validate_audio_file(
        audio_file,
        content,
        "RECORDING",
    )

    assert result == (".webm", "audio/webm")
