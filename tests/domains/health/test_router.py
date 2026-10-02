from fastapi.testclient import TestClient


def test_liveness(client: TestClient) -> None:
    res = client.get("/api/v1/health")

    assert res.status_code == 200
    assert res.json() == {"success": True, "data": {"status": "ok"}}
    assert "X-Trace-Id" in res.headers
