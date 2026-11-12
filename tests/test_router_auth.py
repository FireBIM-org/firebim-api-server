"""Authentication on /graphs, /rules and /checks, IFC graph ownership, and the liveness/readiness probes."""

import pytest

from app.api import graphs as graphs_api
from app.auth.jwt import get_current_active_user
from app.main import app
from app.models.models import IfcModel, User


class _EmptyRepo:
    def list_graphs(self):
        return []

    def fetch_graph(self, graph_id):
        from app.storage.graph_store import GraphStoreNotFoundError

        raise GraphStoreNotFoundError(graph_id)


@pytest.fixture
def anonymous_client(client):
    """The shared client, but without the authenticated-user override."""
    app.dependency_overrides.pop(get_current_active_user, None)
    return client


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/graphs"),
        ("GET", "/graphs/some-graph"),
        ("POST", "/graphs/publish"),
        ("DELETE", "/graphs/some-graph"),
        ("POST", "/graphs/some-graph/answers"),
        ("GET", "/rules/list"),
        ("POST", "/rules/publish"),
        ("POST", "/rules/delete"),
        ("GET", "/rules/rule-elements"),
        ("POST", "/checks/run"),
        ("POST", "/checks/compatibility"),
    ],
)
def test_graph_rule_and_check_endpoints_require_authentication(anonymous_client, method, path):
    response = anonymous_client.request(method, path, json={})
    assert response.status_code == 401


@pytest.mark.parametrize("path", ["/graphs/health", "/rules/health", "/checks/health"])
def test_router_health_endpoints_stay_public(anonymous_client, monkeypatch, path):
    monkeypatch.setattr(graphs_api.repo, "health", lambda: True)
    response = anonymous_client.get(path)
    assert response.status_code == 200


def test_openapi_marks_protected_operations():
    schema = app.openapi()
    assert schema["paths"]["/graphs"]["get"].get("security") == [{"HTTPBearer": []}]
    assert schema["paths"]["/rules/list"]["get"].get("security") == [{"HTTPBearer": []}]
    assert schema["paths"]["/checks/run"]["post"].get("security") == [{"HTTPBearer": []}]
    assert "security" not in schema["paths"]["/graphs/health"]["get"]


def _other_users_model(session) -> IfcModel:
    other = User(authentik_sub="other-sub", username="other", email="other@example.org", is_active=True)
    session.add(other)
    session.commit()
    model = IfcModel(id="other-model", filename="other.ifc", s3_key="models/other-model/other.ifc", user_id=other.id)
    session.add(model)
    session.commit()
    return model


def test_graph_list_only_shows_own_ifc_models(client, session, test_user, monkeypatch):
    monkeypatch.setattr(graphs_api, "repo", _EmptyRepo())
    _other_users_model(session)
    session.add(IfcModel(id="my-model", filename="mine.ifc", s3_key="models/my-model/mine.ifc", user_id=test_user.id))
    session.commit()

    response = client.get("/graphs")

    assert response.status_code == 200
    assert response.json()["graph_uris"] == ["ifc://my-model"]


def test_graph_fetch_hides_other_users_ifc_samples(client, session, monkeypatch):
    monkeypatch.setattr(graphs_api, "repo", _EmptyRepo())
    model = _other_users_model(session)

    response = client.get(f"/graphs/{model.id}")

    assert response.status_code == 404


def test_livez_has_no_dependencies(anonymous_client, mock_s3):
    mock_s3.reset_mock()
    response = anonymous_client.get("/livez")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    mock_s3.head_bucket.assert_not_called()


def test_readyz_checks_database_and_s3_only(client, mock_s3, mock_celery):
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "s3": "ok"}}
    mock_celery.control.ping.assert_not_called()


def test_readyz_fails_when_s3_is_down(client, mock_s3):
    mock_s3.head_bucket.side_effect = RuntimeError("s3 down")
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["s3"] == "error"
