from app.models.models import IfcEntity, IfcModel


def test_model_metadata_counts_stored_graph_and_preserves_profile(client, session, test_user):
    model = IfcModel(id="metadata-model", filename="test.ifc", s3_key="test.ifc", user_id=test_user.id)
    other_model = IfcModel(id="other-model", filename="other.ifc", s3_key="other.ifc", user_id=test_user.id)
    session.add_all([model, other_model])
    session.commit()
    session.add(IfcEntity(global_id="other-root", id="1", model_id=other_model.id, type="IfcProject"))
    session.commit()

    response = client.post(
        f"/ifc-models/{model.id}/data-payload",
        json={
            "entities": [
                {"id": "1", "globalId": "project", "type": "IfcProject"},
                {"id": "2", "globalId": "wall", "type": "IfcWall", "parentId": "1"},
            ],
            "properties": [
                {
                    "entity_id": "2",
                    "property_set_name": "WallCommon",
                    "property_name": "IsExternal",
                    "value": "true",
                    "value_type": "boolean",
                },
            ],
            "conversion_profile_id": "fire-safety",
            "conversion_profile_name": "Fire Safety",
        },
    )
    assert response.status_code == 201

    # Cross-model relationships are not edges in this model's graph.
    session.add(IfcEntity(global_id="cross-model", id="3", model_id=model.id, type="IfcWall", parent_id="other-root"))
    session.commit()
    session.expire_all()

    response = client.get(f"/ifc-models/{model.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["node_count"] == 3
    assert data["edge_count"] == 1
    assert data["conversion_profile_id"] == "fire-safety"
    assert data["conversion_profile_name"] == "Fire Safety"


def test_model_metadata_without_profile_has_zero_counts_and_unknown_profile(client, session, test_user):
    model = IfcModel(id="legacy-model", filename="old.ifc", s3_key="old.ifc", user_id=test_user.id)
    session.add(model)
    session.commit()

    response = client.get(f"/ifc-models/{model.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["node_count"] == 0
    assert data["edge_count"] == 0
    assert data["conversion_profile_id"] is None
    assert data["conversion_profile_name"] is None


def test_model_metadata_requires_ownership(client, session):
    session.add(IfcModel(id="private-model", filename="private.ifc", s3_key="private.ifc", user_id="someone-else"))
    session.commit()

    assert client.get("/ifc-models/private-model").status_code == 403
    assert client.get("/ifc-models/missing-model").status_code == 404
