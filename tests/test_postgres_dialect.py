"""PostgreSQL readiness (Phase 2).

No live PostgreSQL server is available in the test sandbox, so these tests verify
what can be verified without one: the psycopg driver is importable, a PostgreSQL
engine can be constructed, and every model's DDL compiles for the postgresql
dialect (including JSON columns mapping to PG json). The docker-compose stack
provides a real PostgreSQL for integration runs.
"""

from sqlalchemy import JSON, create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from backend.database import models  # noqa: F401  (registers tables)
from backend.database.session import Base


def test_psycopg_driver_available():
    import psycopg  # noqa: F401


def test_engine_accepts_postgres_url():
    engine = create_engine("postgresql+psycopg://shopbot:shopbot@localhost:5432/shopbot")
    assert engine.dialect.name == "postgresql"


def test_all_models_compile_for_postgresql():
    dialect = postgresql.dialect()
    assert len(Base.metadata.sorted_tables) >= 8  # users, sessions, proposals, approvals, orders, listings, executions, audit_events
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert "CREATE TABLE" in ddl
        assert table.name in ddl


def test_json_columns_map_to_postgres_json():
    dialect = postgresql.dialect()
    json_columns = [
        (table.name, col.name)
        for table in Base.metadata.sorted_tables
        for col in table.columns
        if isinstance(col.type, JSON)
    ]
    # proposals.items, orders.items, sessions.data, audit_events.details
    assert len(json_columns) >= 4
    for table in Base.metadata.sorted_tables:
        for col in table.columns:
            if isinstance(col.type, JSON):
                compiled = str(col.type.compile(dialect=dialect)).upper()
                assert compiled in ("JSON", "JSONB")
