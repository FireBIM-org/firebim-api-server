import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlmodel import SQLModel


def test_migration_preserves_legacy_rows_and_matches_admission_metadata():
    path = Path(__file__).parents[1] / "alembic/versions/8c9d0e1f2a3b_add_durable_neo_admission.py"
    spec = importlib.util.spec_from_file_location("admission_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE ifc_models (id VARCHAR PRIMARY KEY)"))
        connection.execute(sa.text("INSERT INTO ifc_models VALUES ('legacy-model')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert connection.execute(sa.text("SELECT ingestion_backend FROM ifc_models")).scalar_one() == "legacy"
        inspector = sa.inspect(connection)
        for name in ("ifc_upload_sessions", "ingestion_jobs", "ingestion_attempts", "ingestion_outbox"):
            assert {column["name"] for column in inspector.get_columns(name)} == set(
                SQLModel.metadata.tables[name].c.keys(),
            )
            assert {item["name"] for item in inspector.get_indexes(name)} == {
                item.name for item in SQLModel.metadata.tables[name].indexes
            }
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert connection.execute(sa.text("SELECT id FROM ifc_models")).scalar_one() == "legacy-model"
        assert sa.inspect(connection).get_table_names() == ["ifc_models"]


def test_publication_migration_retains_jobs_and_matches_ready_graph_metadata():
    migrations = []
    for filename in ("8c9d0e1f2a3b_add_durable_neo_admission.py", "9d0e1f2a3b4c_add_immutable_graph_publications.py"):
        path = Path(__file__).parents[1] / "alembic/versions" / filename
        spec = importlib.util.spec_from_file_location("migration_" + filename[:12], path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migrations.append(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE ifc_models (id VARCHAR PRIMARY KEY)"))
        connection.execute(sa.text("INSERT INTO ifc_models VALUES ('legacy-model')"))
        with Operations.context(MigrationContext.configure(connection)):
            for migration in migrations:
                migration.upgrade()
        inspector = sa.inspect(connection)
        table = SQLModel.metadata.tables["published_graphs"]
        assert {column["name"] for column in inspector.get_columns(table.name)} == set(table.c.keys())
        assert {item["name"] for item in inspector.get_indexes(table.name)} == {item.name for item in table.indexes}
        assert {tuple(item["column_names"]) for item in inspector.get_unique_constraints(table.name)} == {
            ("graph_uri",),
            ("job_id",),
        }
        with Operations.context(MigrationContext.configure(connection)):
            migrations[1].downgrade()
        assert "ingestion_jobs" in sa.inspect(connection).get_table_names()
        assert connection.execute(sa.text("SELECT id FROM ifc_models")).scalar_one() == "legacy-model"
