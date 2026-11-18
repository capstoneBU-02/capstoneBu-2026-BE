from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.exceptions import BusinessException, ErrorCode
from app.domains.input.models import Track
from app.domains.segment import service
from app.domains.segment.router import (
    VerifiedTrack,
    get_verified_track,
    router,
)
from app.domains.segment.schemas import SegmentResponse
from app.main import create_app


def test_create_segment_route_exists() -> None:
    routes = [
        route
        for route in router.routes
        if getattr(route, "path", None) == "/tracks/{track_id}/segments"
    ]

    assert len(routes) == 1
    assert "POST" in routes[0].methods
    assert routes[0].status_code == 201


def test_create_segment_requires_authentication() -> None:
    app = create_app()

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/tracks/1/segments",
            json={"start_ms": 10500, "end_ms": 25800},
        )

    assert response.status_code == 401
    assert response.json()["success"] is False
    assert response.json()["error"]["code"] == "UNAUTHORIZED_SESSION"


def test_create_segment_returns_201(monkeypatch) -> None:
    app = create_app()
    fake_db = Mock()

    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_verified_track] = lambda: VerifiedTrack(
        track_id=1,
        duration_ms=60000,
    )

    expected = SegmentResponse(
        segment_id=10,
        track_id=1,
        start_ms=10500,
        end_ms=25800,
        duration_ms=15300,
        name=None,
        version=1,
        created_at=datetime(2026, 11, 10, tzinfo=UTC),
        updated_at=datetime(2026, 11, 10, tzinfo=UTC),
    )

    create_mock = Mock(return_value=expected)
    monkeypatch.setattr(service, "create_segment", create_mock)

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/tracks/1/segments",
                json={"start_ms": 10500, "end_ms": 25800},
            )

        assert response.status_code == 201

        body = response.json()
        assert body["success"] is True
        assert body["data"]["segment_id"] == 10
        assert body["data"]["track_id"] == 1
        assert body["data"]["duration_ms"] == 15300
        assert body["data"]["version"] == 1

        create_mock.assert_called_once()
    finally:
        app.dependency_overrides.clear()


def test_create_segment_rejects_range_exceeding_track_duration(
    monkeypatch,
) -> None:
    app = create_app()
    fake_db = Mock()

    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_verified_track] = lambda: VerifiedTrack(
        track_id=1,
        duration_ms=60000,
    )

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/tracks/1/segments",
                json={
                    "start_ms": 50000,
                    "end_ms": 70000,
                },
            )

        assert response.status_code == 400
        assert response.json()["success"] is False
        assert response.json()["error"]["code"] == "INVALID_SEGMENT_RANGE"
    finally:
        app.dependency_overrides.clear()


def test_create_segment_rejects_start_greater_than_or_equal_to_end() -> None:
    app = create_app()
    fake_db = Mock()

    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_verified_track] = lambda: VerifiedTrack(
        track_id=1,
        duration_ms=60000,
    )

    try:
        with TestClient(app) as client:
            for start_ms, end_ms in [(5000, 5000), (7000, 3000)]:
                response = client.post(
                    "/api/v1/tracks/1/segments",
                    json={
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                    },
                )

                assert response.status_code == 400
                assert response.json()["error"]["code"] == "INVALID_SEGMENT_RANGE"
    finally:
        app.dependency_overrides.clear()


def test_verified_track_accepts_own_workspace() -> None:
    db = Mock()
    workspace = Mock()
    workspace.id = 10

    track = Mock()
    track.id = 1
    track.workspace_id = 10
    track.duration_ms = 60000
    db.get.return_value = track

    result = get_verified_track(
        track_id=1,
        db=db,
        workspace=workspace,
    )

    assert result == VerifiedTrack(track_id=1, duration_ms=60000)
    db.get.assert_called_once_with(Track, 1)


def test_verified_track_rejects_missing_track() -> None:
    db = Mock()
    db.get.return_value = None

    workspace = Mock()
    workspace.id = 10

    with pytest.raises(BusinessException) as exc:
        get_verified_track(
            track_id=999,
            db=db,
            workspace=workspace,
        )

    assert exc.value.error_code == ErrorCode.TRACK_NOT_FOUND


def test_verified_track_rejects_other_workspace() -> None:
    db = Mock()
    workspace = Mock()
    workspace.id = 10

    track = Mock()
    track.id = 1
    track.workspace_id = 20
    db.get.return_value = track

    with pytest.raises(BusinessException) as exc:
        get_verified_track(
            track_id=1,
            db=db,
            workspace=workspace,
        )

    assert exc.value.error_code == ErrorCode.TRACK_NOT_FOUND


def test_update_segment_route_exists() -> None:
    routes = [
        route for route in router.routes if getattr(route, "path", None) == "/segments/{segment_id}"
    ]

    assert len(routes) == 1
    assert "PATCH" in routes[0].methods
