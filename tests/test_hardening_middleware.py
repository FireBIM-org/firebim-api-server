"""Body size limits, security headers, the generic 500 handler, error codes and the API docs toggle."""

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.body_limit import BodySizeLimitMiddleware
from app.core.errors import install_error_handlers, payload_too_large
from app.core.security_headers import SecurityHeadersMiddleware


class Payload(BaseModel):
    text: str


def _body_app(*, max_json: int = 100, max_upload: int = 1000, overhead: int = 300) -> FastAPI:
    app = FastAPI()

    @app.post("/json")
    def json_endpoint(payload: Payload):
        return {"length": len(payload.text)}

    @app.post("/raw")
    async def raw_endpoint(request: Request):
        return {"length": len(await request.body())}

    @app.post("/upload")
    async def upload_endpoint(file: UploadFile = File(...)):
        return {"size": file.size}

    app.add_middleware(
        BodySizeLimitMiddleware,
        max_json_body_bytes=max_json,
        max_upload_bytes=max_upload,
        multipart_overhead_bytes=overhead,
    )
    return app


def test_json_body_within_limit_passes():
    client = TestClient(_body_app())
    response = client.post("/json", json={"text": "x" * 50})
    assert response.status_code == 200
    assert response.json() == {"length": 50}


def test_declared_content_length_over_limit_is_rejected_up_front():
    client = TestClient(_body_app())
    response = client.post("/json", json={"text": "x" * 500})
    assert response.status_code == 413
    assert response.json()["code"] == "PAYLOAD_TOO_LARGE"


def test_chunked_body_is_counted_while_streaming():
    client = TestClient(_body_app())

    def chunks():
        for _ in range(10):
            yield b"y" * 30

    # A generator body is sent with Transfer-Encoding: chunked and no Content-Length.
    response = client.post("/raw", content=chunks())
    assert response.status_code == 413
    assert response.json()["code"] == "PAYLOAD_TOO_LARGE"

    small = client.post("/raw", content=iter([b"y" * 30, b"y" * 30]))
    assert small.status_code == 200
    assert small.json() == {"length": 60}


def test_chunked_json_body_turns_parse_failure_into_413():
    client = TestClient(_body_app())
    response = client.post(
        "/json",
        content=iter([b'{"text": "', b"z" * 200, b'"}']),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_multipart_uploads_get_the_upload_limit_plus_overhead():
    client = TestClient(_body_app())
    ok = client.post("/upload", files={"file": ("model.ifc", b"a" * 900)})
    assert ok.status_code == 200
    assert ok.json() == {"size": 900}

    too_big = client.post("/upload", files={"file": ("model.ifc", b"a" * 1400)})
    assert too_big.status_code == 413
    assert too_big.json()["code"] == "PAYLOAD_TOO_LARGE"


def _headers_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/data")
    def data():
        return {"ok": True}

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret connection string postgres://user:pw@db")

    @app.get("/too-large")
    def too_large():
        raise payload_too_large("too big")

    app.add_middleware(SecurityHeadersMiddleware)
    return app


def test_security_headers_and_no_store_on_authenticated_json():
    client = TestClient(_headers_app())
    anonymous = client.get("/data")
    assert anonymous.headers["X-Content-Type-Options"] == "nosniff"
    assert anonymous.headers["X-Frame-Options"] == "DENY"
    assert anonymous.headers["Referrer-Policy"] == "no-referrer"
    assert "cache-control" not in anonymous.headers

    authenticated = client.get("/data", headers={"Authorization": "Bearer t"})
    assert authenticated.headers["Cache-Control"] == "no-store"


def test_unhandled_exception_returns_generic_500_with_request_id(caplog):
    client = TestClient(_headers_app(), raise_server_exceptions=False)
    response = client.get("/boom", headers={"X-Request-ID": "req-123"})

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error", "request_id": "req-123"}
    assert "secret" not in response.text
    assert response.headers["X-Request-ID"] == "req-123"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert any("req-123" in record.getMessage() and record.exc_info for record in caplog.records)


def test_api_error_renders_code():
    client = TestClient(_headers_app())
    response = client.get("/too-large")
    assert response.status_code == 413
    assert response.json() == {"detail": "too big", "code": "PAYLOAD_TOO_LARGE"}


def test_main_app_500_uses_request_id_from_middleware(client):
    from app.main import app

    @app.get("/__test_boom")
    def _boom():
        raise RuntimeError("do not leak me")

    try:
        response = client.get("/__test_boom")
    finally:
        app.router.routes = [route for route in app.router.routes if getattr(route, "path", "") != "/__test_boom"]

    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == "Internal server error"
    assert body["request_id"] == response.headers["X-Request-ID"]
    assert "do not leak me" not in response.text


def test_api_docs_toggle():
    from app.main import api_docs_urls, app

    assert app.docs_url == "/docs" and app.openapi_url == "/openapi.json"
    assert api_docs_urls(False) == {"docs_url": None, "redoc_url": None, "openapi_url": None}
    assert api_docs_urls(True) == {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}
