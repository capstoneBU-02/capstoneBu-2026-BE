"""기능(도메인)별 폴더. 새 도메인을 추가하는 규칙은 README.md 참고."""

import importlib.util
import pkgutil


def find_domain_modules(module_name: str) -> list[str]:
    """모든 도메인 폴더에서 같은 이름의 모듈(예: models, tasks) 경로를 찾는다.

    Alembic(엔티티 인식)과 Celery(작업 등록)가 도메인을 하나씩 등록하지 않아도 되게 한다.
    예) ["app.domains.songs.models", "app.domains.jobs.models"]
    """
    found = []
    for domain in pkgutil.iter_modules(__path__):
        if not domain.ispkg:
            continue
        name = f"{__name__}.{domain.name}.{module_name}"
        if importlib.util.find_spec(name) is not None:
            found.append(name)
    return found
