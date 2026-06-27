from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.routes import router
from app.services.request_service import RequestConflictError, RequestNotFoundError, RequestPublicationError
from app_shared.metrics import ApiMetrics
from app_shared.observability import configure_logging
from app_shared.request_status import FACEBOOK_QUERY_SOURCE, FACEBOOK_SOURCE_TYPE, RequestStatusCode


class StubService:
    def __init__(
        self,
        *,
        should_fail: bool = False,
        should_conflict: bool = False,
        not_found: bool = False,
        status_current: RequestStatusCode = RequestStatusCode.PENDING,
    ):
        self.should_fail = should_fail
        self.should_conflict = should_conflict
        self.not_found = not_found
        self.status_current = status_current
        self.request_id = uuid4()
        self.user_id = uuid4()
        self.created_at = datetime.now(timezone.utc)

    def create_request(self, client_ip: str, request_data: dict):
        if self.should_conflict:
            raise RequestConflictError(request_data["id_query"])
        metadata = {
            "request_id": self.request_id,
            "id_query": request_data["id_query"],
            "user_id": self.user_id,
            "ip_request": client_ip,
            "numero_requests": 1,
            "subject": request_data["subject"],
            "query_source": FACEBOOK_QUERY_SOURCE,
            "source_type": FACEBOOK_SOURCE_TYPE,
            "status_current": RequestStatusCode.FAILED if self.should_fail else self.status_current,
            "created_at": self.created_at,
            "updated_at": self.created_at,
            "start_date": request_data.get("start_date"),
            "end_date": request_data.get("end_date"),
        }
        if self.should_fail:
            raise RequestPublicationError(metadata)
        return {
            "metadata": metadata,
            "data": {
                "profiles": [],
                "posts": [],
                "videos": [],
                "pages": [],
                "groups": [],
                "items": [],
            },
        }

    def get_request(self, request_id):
        if self.not_found:
            raise RequestNotFoundError(f"Request {request_id} not found")
        return self._build_response(request_id, 45)

    def get_request_by_query(self, id_query):
        if self.not_found:
            raise RequestNotFoundError(f"Request with id_query {id_query} not found")
        return self._build_response(self.request_id, id_query)

    def _build_response(self, request_id, id_query):
        return {
            "metadata": {
                "request_id": request_id,
                "id_query": id_query,
                "user_id": self.user_id,
                "ip_request": "127.0.0.1",
                "numero_requests": 2,
                "subject": "facebook gui ai",
                "query_source": FACEBOOK_QUERY_SOURCE,
                "source_type": FACEBOOK_SOURCE_TYPE,
                "status_current": self.status_current,
                "created_at": self.created_at,
                "updated_at": self.created_at,
                "start_date": datetime(2025, 1, 1, tzinfo=timezone.utc),
                "end_date": datetime(2025, 12, 31, tzinfo=timezone.utc),
            },
            "data": {
                "profiles": [],
                "posts": [],
                "videos": [],
                "pages": [],
                "groups": [],
                "items": [],
            },
        }


def build_app(stub_service, monkeypatch, allowed_hosts=None):
    app = FastAPI()
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts or ["*"]))
    app.include_router(router)
    app.state.logger = configure_logging("api-test", "INFO")
    app.state.api_metrics = ApiMetrics()
    monkeypatch.setattr("app.api.routes.get_request_service", lambda request: stub_service)
    return app


def test_post_requests_returns_201(monkeypatch):
    app = build_app(StubService(), monkeypatch)

    with TestClient(app) as client:
        response = client.post(
            "/requests",
            json={
                "id_query": "45",
                "subject": "facebook gui ai",
                "start_date": "2025-01-01T00:00:00Z",
                "end_date": "2025-12-31T23:59:59Z",
                "status_current": 1,
                "query_source": 10,
            },
        )

    assert response.status_code == 201
    assert response.json()["metadata"]["id_query"] == 45
    assert response.json()["metadata"]["status_current"] == 1
    assert response.json()["data"]["items"] == []


def test_post_requests_rejects_disallowed_host(monkeypatch):
    app = build_app(StubService(), monkeypatch, allowed_hosts=["api.local"])

    with TestClient(app) as client:
        response = client.post(
            "/requests",
            headers={"host": "evil.local"},
            json={"id_query": "45", "subject": "facebook gui ai", "status_current": 1},
        )

    assert response.status_code == 400


def test_post_requests_accepts_allowed_host(monkeypatch):
    app = build_app(StubService(), monkeypatch, allowed_hosts=["api.local"])

    with TestClient(app) as client:
        response = client.post(
            "/requests",
            headers={"host": "api.local"},
            json={"id_query": "45", "subject": "facebook gui ai", "status_current": 1},
        )

    assert response.status_code == 201


def test_post_requests_accepts_single_item_list(monkeypatch):
    app = build_app(StubService(), monkeypatch)

    with TestClient(app) as client:
        response = client.post(
            "/requests",
            json=[
                {
                    "id_query": "45",
                    "subject": "facebook gui ai",
                    "status_current": 1,
                }
            ],
        )

    assert response.status_code == 201
    assert response.json()["metadata"]["id_query"] == 45


def test_post_requests_rejects_multi_item_list(monkeypatch):
    app = build_app(StubService(), monkeypatch)

    with TestClient(app) as client:
        response = client.post(
            "/requests",
            json=[
                {"id_query": "45", "subject": "facebook gui ai", "status_current": 1},
                {"id_query": "46", "subject": "openai", "status_current": 1},
            ],
        )

    assert response.status_code == 422


def test_post_requests_returns_503_when_publish_fails(monkeypatch):
    app = build_app(StubService(should_fail=True), monkeypatch)

    with TestClient(app) as client:
        response = client.post(
            "/requests",
            json={"id_query": "45", "subject": "facebook gui ai", "status_current": 1},
        )

    assert response.status_code == 503
    assert response.json()["metadata"]["status_current"] == 5


def test_post_requests_returns_409_for_duplicate_id_query(monkeypatch):
    app = build_app(StubService(should_conflict=True), monkeypatch)

    with TestClient(app) as client:
        response = client.post(
            "/requests",
            json={"id_query": "45", "subject": "facebook gui ai", "status_current": 1},
        )

    assert response.status_code == 409
    assert response.json()["id_query"] == 45


def test_post_requests_rejects_blank_subject(monkeypatch):
    app = build_app(StubService(), monkeypatch)

    with TestClient(app) as client:
        response = client.post(
            "/requests",
            json={"id_query": "45", "subject": "   ", "status_current": 1},
        )

    assert response.status_code == 422


def test_post_requests_rejects_non_pending_status(monkeypatch):
    app = build_app(StubService(), monkeypatch)

    with TestClient(app) as client:
        response = client.post(
            "/requests",
            json={"id_query": "45", "subject": "facebook gui ai", "status_current": 2},
        )

    assert response.status_code == 422


def test_post_requests_requires_status_current(monkeypatch):
    app = build_app(StubService(), monkeypatch)

    with TestClient(app) as client:
        response = client.post(
            "/requests",
            json={"id_query": "45", "subject": "facebook gui ai"},
        )

    assert response.status_code == 422


def test_get_requests_returns_404_when_missing(monkeypatch):
    app = build_app(StubService(not_found=True), monkeypatch)

    with TestClient(app) as client:
        response = client.get(f"/requests/{uuid4()}")

    assert response.status_code == 404


def test_get_request_by_query_returns_metadata_and_data(monkeypatch):
    app = build_app(StubService(status_current=RequestStatusCode.PROCESSING), monkeypatch)

    with TestClient(app) as client:
        response = client.get("/requests/query/45")

    assert response.status_code == 200
    assert response.json()["metadata"]["status_current"] == 2
    assert response.json()["data"]["profiles"] == []


def test_metrics_route_returns_prometheus_payload(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    app.state.logger = configure_logging("api-test", "INFO")
    app.state.api_metrics = ApiMetrics()
    monkeypatch.setattr(
        "app.api.routes.render_metrics",
        lambda app_instance: Response("test_metric 1\n", media_type="text/plain"),
    )

    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    assert "test_metric 1" in response.text
