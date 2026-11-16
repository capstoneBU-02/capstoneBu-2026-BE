"""다른 도메인(SEG·IN·ACC)에서 읽어 오는 값.

구간은 SEG의 segments 테이블, 세션은 ACC의 get_current_workspace에서 읽는다.
트랙(IN)은 아직 없어서 load_track만 임시로 두고, 모델이 들어오면 그 함수 본문만 바꾼다.
"""

from dataclasses import dataclass

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException, ErrorCode
from app.domains.account.deps import get_current_workspace
from app.domains.account.models import Workspace
from app.domains.segment.models import Segment


@dataclass(frozen=True)
class TrackSource:
    """기타 음원 준비에 필요한 음원 정보 (IN tracks)."""

    track_id: int
    workspace_id: int  # 소유권 확인 기준 (segments에는 workspace_id가 없다)
    is_separated_stem: bool
    storage_key: str  # 원본 파일 경로
    file_hash: str  # input_hash 계산용


@dataclass(frozen=True)
class SegmentSource:
    """기타 음원 준비에 필요한 구간·음원 정보."""

    workspace_id: int
    track_id: int
    segment_id: int
    start_ms: int  # 현재 구간 범위
    end_ms: int
    is_separated_stem: bool  # tracks.is_separated_stem
    track_storage_key: str  # 원본 파일 경로
    track_file_hash: str  # 원본 파일 해시 (input_hash 계산용)
    name: str | None = None  # 구성 이름 (INTRO, VERSE ...). 비교 결과 표시에 쓴다


def load_track(db: Session, track_id: int) -> TrackSource:
    """트랙을 조회한다. 없으면 SEGMENT_NOT_FOUND (구간의 음원이 없는 것과 같다).

    TODO(IN): tracks 모델이 머지되면 조회로 교체 (이슈 #19).
    """
    raise BusinessException(ErrorCode.SEGMENT_NOT_FOUND, "트랙 조회가 아직 연결되지 않았습니다.")


def load_segment(
    db: Session, workspace_id: int, segment_id: int, *, lock: bool = False
) -> SegmentSource:
    """구간과 그 음원을 조회한다. 없거나 다른 워크스페이스 소유면 SEGMENT_NOT_FOUND.

    lock이면 구간 행을 공유 잠금(FOR SHARE)으로 읽는다. 작업을 만드는 동안 구간 수정이
    끝나기를 기다리게 해서, 읽은 범위와 저장되는 범위가 어긋나지 않게 한다.
    세션에 남은 예전 값이 아니라 항상 DB의 최신 값을 읽는다.
    """
    stmt = select(Segment).where(Segment.id == segment_id)
    if lock:
        stmt = stmt.with_for_update(read=True)
    segment = db.scalars(stmt.execution_options(populate_existing=True)).first()
    if segment is None:
        raise BusinessException(ErrorCode.SEGMENT_NOT_FOUND)
    track = load_track(db, segment.track_id)
    if track.workspace_id != workspace_id:
        raise BusinessException(ErrorCode.SEGMENT_NOT_FOUND)  # 다른 워크스페이스는 없는 것처럼
    return SegmentSource(
        workspace_id=track.workspace_id,
        track_id=segment.track_id,
        segment_id=segment.id,
        start_ms=segment.start_ms,
        end_ms=segment.end_ms,
        is_separated_stem=track.is_separated_stem,
        track_storage_key=track.storage_key,
        track_file_hash=track.file_hash,
        name=segment.name,
    )


def load_track_storage_key(db: Session, track_id: int) -> str:
    """원본 파일 경로. worker의 preprocess와 CMP 재생 정보가 쓴다."""
    return load_track(db, track_id).storage_key


def get_current_workspace_id(workspace: Workspace = Depends(get_current_workspace)) -> int:
    """세션 쿠키(ws_token)로 확인한 워크스페이스 ID. 없거나 만료면 401 UNAUTHORIZED_SESSION."""
    return workspace.id
