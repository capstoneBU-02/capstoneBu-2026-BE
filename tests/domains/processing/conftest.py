from collections.abc import Iterator

import pytest

from app.domains.processing.models import Stem
from tests.domains.conftest import EnqueueSpy, make_enqueue


@pytest.fixture
def enqueue(request: pytest.FixtureRequest) -> Iterator[EnqueueSpy]:
    yield from make_enqueue(request, Stem)
