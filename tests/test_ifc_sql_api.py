import pytest
from sqlmodel import Session, select
from app.models.models import IfcModel, User, IfcEntity, IfcProperty, IfcRelationship

@pytest.mark.integration
def test_upload_ifc_sql_data(client, session: Session, test_user: User):
    # 1. Setup: Create an IFC model record
    model = IfcModel(
        id="test-model-sql",
        filename="test.ifc",
        s3_key="models/test/test.ifc",
        user_id=test_user.id,
        processing_status="completed"
    )
    session.add(model)
    session.commit()

    # 2. Payload for the new endpoint
    payload = {
        "entities": [
            {
                "id": "1",
                "type": "IfcWall",
                "globalId": "3$d$7$9$b$d$f$h$j$l$n$p$r$t$v$x$z",
                "name": "Wall 1",
                "attributes": {"OverallHeight": 3000}
            }
        ],
        "properties": [
            {
                "entity_id": "1",
                "property_set_name": "Pset_WallCommon",
                "property_name": "IsExternal",
                "value": "TRUE",
                "value_type": "IfcBoolean"
            }
        ],
        "relationships": [
            {
                "type": "IfcRelAggregates",
                "relating_id": "0", # Assuming project or similar
                "related_id": "1"
            }
        ]
    }

    # 3. Call the endpoint
    response = client.post(f"/ifc-models/{model.id}/sql-data", json=payload)

    # 4. Assertions
    assert response.status_code == 201
    data = response.json()
    assert data["message"] == "Data uploaded successfully"
    assert data["entity_count"] == 1
    assert data["property_count"] == 1
    assert data["relationship_count"] == 1

    # 5. Verify DB state
    entities = session.exec(select(IfcEntity).where(IfcEntity.model_id == model.id)).all()
    assert len(entities) == 1
    assert entities[0].type == "IfcWall"
    assert entities[0].global_id == "3$d$7$9$b$d$f$h$j$l$n$p$r$t$v$x$z"
    assert entities[0].attributes == {"OverallHeight": 3000}

    properties = session.exec(select(IfcProperty).where(IfcProperty.entity_id == "1")).all()
    assert len(properties) == 1
    assert properties[0].property_name == "IsExternal"
    assert properties[0].value == "TRUE"

    relationships = session.exec(select(IfcRelationship).where(IfcRelationship.model_id == model.id)).all()
    assert len(relationships) == 1
    assert relationships[0].type == "IfcRelAggregates"
    assert relationships[0].relating_id == "0"
    assert relationships[0].related_id == "1"


@pytest.mark.integration
def test_upload_ifc_sql_data_bulk(client, session: Session, test_user: User):
    # Setup: Create an IFC model record
    model_id = "test-model-bulk"
    model = IfcModel(
        id=model_id,
        filename="bulk.ifc",
        s3_key="models/test/bulk.ifc",
        user_id=test_user.id,
        processing_status="completed"
    )
    session.add(model)
    session.commit()

    # Generate larger payload
    num_entities = 100
    payload = {
        "entities": [
            {
                "id": str(i),
                "type": "IfcWall",
                "globalId": f"global-id-{i}",
                "name": f"Wall {i}",
                "attributes": {"height": 3000}
            } for i in range(num_entities)
        ],
        "properties": [
            {
                "entity_id": str(i),
                "property_set_name": "Pset_WallCommon",
                "property_name": "IsExternal",
                "value": "TRUE",
                "value_type": "IfcBoolean"
            } for i in range(num_entities)
        ],
        "relationships": [
            {
                "type": "IfcRelAggregates",
                "relating_id": "0",
                "related_id": str(i)
            } for i in range(num_entities)
        ]
    }

    # Call the endpoint
    response = client.post(f"/ifc-models/{model_id}/sql-data", json=payload)

    # Assertions
    assert response.status_code == 201
    data = response.json()
    assert data["entity_count"] == num_entities
    assert data["property_count"] == num_entities
    assert data["relationship_count"] == num_entities

    # Verify DB state
    entities_count = session.exec(select(IfcEntity).where(IfcEntity.model_id == model_id)).all()
    assert len(entities_count) == num_entities

    properties_count = session.exec(select(IfcProperty)).all()
    # Note: might have other properties from previous tests if session is not cleared, 
    # but the fixture usually handles it. Assuming fresh session or filtering by entity_ids.
    # Better filter by entity_id
    all_entity_ids = [str(i) for i in range(num_entities)]
    properties = session.exec(select(IfcProperty).where(IfcProperty.entity_id.in_(all_entity_ids))).all()
    assert len(properties) == num_entities


@pytest.mark.integration
def test_upload_ifc_sql_data_integrity(client, session: Session, test_user: User):
    # Setup: Create an IFC model record
    model_id = "test-model-integrity"
    model = IfcModel(
        id=model_id,
        filename="integrity.ifc",
        s3_key="models/test/integrity.ifc",
        user_id=test_user.id,
        processing_status="completed"
    )
    session.add(model)
    session.commit()

    # Payload with invalid entity_id in properties (Foreign Key violation)
    payload = {
        "entities": [
            {
                "id": "valid-ent",
                "type": "IfcWall",
                "globalId": "gid-1",
                "name": "Wall 1",
                "attributes": {}
            }
        ],
        "properties": [
            {
                "entity_id": "non-existent-ent", # This should cause FK error
                "property_set_name": "Pset",
                "property_name": "Prop",
                "value": "Val",
                "value_type": "Str"
            }
        ],
        "relationships": []
    }

    # Call the endpoint
    response = client.post(f"/ifc-models/{model_id}/sql-data", json=payload)

    # Assertions: Should fail (Internal Server Error due to FK violation)
    # Depending on how the app handles DB errors, it might be 500 or something else.
    assert response.status_code == 500

    # Verify DB state: "valid-ent" should NOT be in the DB due to rollback
    # We need a new session or refresh because the original session might be in error state
    session.rollback() # Clear state if needed, though 'session' fixture usually handles it
    entity = session.exec(select(IfcEntity).where(IfcEntity.id == "valid-ent")).first()
    assert entity is None
