"""도메인별 DB 잠금 키(pg_advisory_xact_lock 첫 번째 값)가 겹치지 않는다 (이슈 #54)."""

from app.domains.compare import service as compare_service
from app.domains.processing import service as processing_service
from app.domains.tab import service as tab_service


def test_lock_classes_are_unique() -> None:
    classes = [
        processing_service._LOCK_CLASS_STEMS,
        tab_service._LOCK_CLASS_TABS,
        compare_service._LOCK_CLASS_COMPARISONS,
    ]
    assert len(set(classes)) == len(classes)
