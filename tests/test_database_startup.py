import pytest
from sqlalchemy import event, inspect, text
from sqlmodel import SQLModel, StaticPool, create_engine

from app.models import database


@pytest.mark.parametrize("stamped", [False, True])
def test_legacy_schema_fails_before_creating_any_new_tables(monkeypatch, stamped):
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE ifc_models (id VARCHAR PRIMARY KEY)"))
        connection.execute(text("INSERT INTO ifc_models VALUES ('legacy-model')"))
        if stamped:
            connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR PRIMARY KEY)"))
            connection.execute(text("INSERT INTO alembic_version VALUES ('7b8c9d0e1f2a')"))
    before = inspect(engine).get_table_names()
    statements = []
    event.listen(engine, "before_cursor_execute", lambda conn, cursor, sql, *args: statements.append(sql))
    monkeypatch.setattr(database, "engine", engine)

    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        database.init_db()

    assert inspect(engine).get_table_names() == before
    assert not any(sql.lstrip().upper().startswith(("CREATE", "ALTER", "DROP")) for sql in statements)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT id FROM ifc_models")).scalar_one() == "legacy-model"


@pytest.mark.parametrize(
    "damage",
    [
        "ALTER TABLE ifc_models DROP COLUMN latest_job_id",
        "DROP TABLE ingestion_outbox",
    ],
)
def test_incomplete_schema_is_rejected_without_repairing_it(monkeypatch, damage):
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text(damage))
    before = inspect(engine).get_table_names()
    monkeypatch.setattr(database, "engine", engine)
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        database.init_db()
    assert inspect(engine).get_table_names() == before


def test_current_schema_and_fresh_bootstrap_remain_supported(monkeypatch):
    engine = create_engine("sqlite://")
    monkeypatch.setattr(database, "engine", engine)
    database.init_db()
    database.init_db()
    assert set(inspect(engine).get_table_names()) == set(SQLModel.metadata.tables)


def test_lifespan_stops_before_storage_or_seeding_on_schema_error(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE ifc_models (id VARCHAR PRIMARY KEY)"))
    monkeypatch.setattr(database, "engine", engine)
    with pytest.raises(RuntimeError, match="alembic upgrade head"), TestClient(main.app):
        pass
    main.s3_client.head_bucket.assert_not_called()
