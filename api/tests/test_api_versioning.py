"""Hard cut to /api/v1: unversioned paths are rejected, callbacks redirect."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from akashic.main import create_app


@pytest.fixture(autouse=True)
def setup_db():  # noqa: D401 - overrides conftest's DB fixture; no DB needed
    yield


@pytest.fixture(scope="module")
def raw_client():
    # No lifespan: these paths are answered before any router or DB is touched.
    return TestClient(create_app(), follow_redirects=False)


@pytest.mark.parametrize("method", ["get", "post", "put", "patch", "delete"])
def test_unversioned_api_is_gone(raw_client, method):
    r = getattr(raw_client, method)("/api/sources")
    assert r.status_code == 410
    body = r.json()
    assert body["code"] == "api_version_required"
    assert "/api/v1/sources" in body["message"]
    assert body["request_id"] == r.headers["x-request-id"]


def test_unversioned_api_root_is_gone(raw_client):
    assert raw_client.get("/api/").status_code == 410
    assert raw_client.get("/api").status_code == 410


def test_unversioned_spec_is_gone(raw_client):
    assert raw_client.get("/api/openapi.json").status_code == 410


def test_docs_are_not_gone(raw_client):
    assert raw_client.get("/api/docs").status_code == 200
    assert raw_client.get("/api/docs/assets/swagger-ui-bundle.js").status_code == 200
    assert raw_client.get("/api/v1/openapi.json").status_code == 200


def test_versioned_unknown_path_is_404_not_410(raw_client):
    assert raw_client.get("/api/v1/nope").status_code == 404


@pytest.mark.parametrize("old,new", [
    ("/api/auth/oidc/callback", "/api/v1/auth/oidc/callback"),
    ("/api/oauth/callback", "/api/v1/oauth/callback"),
])
def test_callbacks_redirect_with_query(raw_client, old, new):
    r = raw_client.get(f"{old}?code=abc&state=xyz")
    assert r.status_code == 308
    assert r.headers["location"] == f"{new}?code=abc&state=xyz"
    assert not r.content  # plain redirect, no error envelope


def test_callback_redirect_is_get_only(raw_client):
    assert raw_client.post("/api/oauth/callback").status_code == 410


def test_unversioned_websocket_rejected(raw_client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with raw_client.websocket_connect("/ws/scans"):
            pass
    assert exc.value.code == 1008
