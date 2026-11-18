"""도메인별 DB 잠금 키가 겹치지 않는다 (이슈 #54). 키는 app/core/locks.py 한곳에서만 정한다."""

from app.core.locks import LockClass
from app.domains.compare import service as compare_service
from app.domains.processing import service as processing_service
from app.domains.tab import service as tab_service


def test_domains_use_registry() -> None:
    assert processing_service._LOCK_CLASS_STEMS is LockClass.STEMS
    assert tab_service._LOCK_CLASS_TABS is LockClass.TABS
    assert compare_service._LOCK_CLASS_COMPARISONS is LockClass.COMPARISONS


def test_lock_classes_are_unique() -> None:
    # @unique라 같은 값을 넣으면 import 시점에 실패한다. 값 개수로 한 번 더 확인
    assert len({c.value for c in LockClass}) == len(LockClass)
