from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlmodel import SQLModel

from app.storage import quota

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "d9553d63ba7d_add_storage_usage_tables.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("storage_usage_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine, step: str) -> None:
    migration = _load_migration()
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        getattr(migration, step)()


def _columns(engine) -> dict[str, set[str]]:
    inspector = inspect(engine)
    return {
        table.name: {column["name"] for column in inspector.get_columns(table.name)}
        for table in quota.ACCOUNTING_TABLES
    }


def test_migration_is_chained_on_the_previous_head():
    migration = _load_migration()
    assert migration.down_revision == "1e9e75ea63ae"


@pytest.mark.parametrize("create_all_first", [False, True])
def test_migration_matches_the_models_and_is_idempotent(create_all_first):
    expected = {table.name: {column.name for column in table.columns} for table in quota.ACCOUNTING_TABLES}
    engine = create_engine("sqlite://")
    if create_all_first:
        SQLModel.metadata.create_all(engine, tables=list(quota.ACCOUNTING_TABLES))

    _run(engine, "upgrade")
    _run(engine, "upgrade")  # no-op the second time

    assert _columns(engine) == expected
    _run(engine, "downgrade")
    assert not any(inspect(engine).has_table(name) for name in expected)
