"""Explicit provider Alembic migration and validated adoption of initial development schema."""
import argparse
import os

from alembic.config import Config
from sqlalchemy import inspect

from aftersales.db import create_session_factory
from alembic import command
from demo_provider import service
from demo_provider.models import Base, Environment


def assert_existing_v1(factory):
    engine = factory.kw["bind"]
    inspector = inspect(engine)
    expected = set(Base.metadata.tables)
    if set(inspector.get_table_names()) != expected:
        raise ValueError("existing provider tables differ; refusing automatic adoption")
    for name, table in Base.metadata.tables.items():
        actual = {column["name"]: column for column in inspector.get_columns(name)}
        if set(actual) != set(table.columns.keys()):
            raise ValueError("existing provider columns differ; refusing adoption")
        for column in table.columns:
            observed = actual[column.name]
            if (str(observed["type"].compile(dialect=engine.dialect)) != str(column.type.compile(dialect=engine.dialect))
                    or observed["nullable"] != column.nullable):
                raise ValueError("existing provider column type/nullability differs")
        if inspector.get_pk_constraint(name)["constrained_columns"] != [column.name for column in table.primary_key]:
            raise ValueError("existing provider primary key differs")
        observed_unique = {tuple(item["column_names"]) for item in inspector.get_unique_constraints(name)}
        expected_unique = {tuple(column.name for column in item.columns) for item in table.constraints
                           if item.__class__.__name__ == "UniqueConstraint"}
        if observed_unique != expected_unique:
            raise ValueError("existing provider unique constraints differ")
        observed_checks = {item["name"] for item in inspector.get_check_constraints(name)}
        expected_checks = {item.name for item in table.constraints if item.__class__.__name__ == "CheckConstraint"}
        if observed_checks != expected_checks:
            raise ValueError("existing provider check constraints differ")
        observed_indexes = {(item["name"], tuple(item["column_names"])) for item in inspector.get_indexes(name)
                            if not item.get("duplicates_constraint")}
        expected_indexes = {(item.name, tuple(column.name for column in item.columns)) for item in table.indexes}
        if observed_indexes != expected_indexes:
            raise ValueError("existing provider indexes differ")
    with factory() as session:
        marker = session.get(Environment, 1)
        if marker is None or marker.schema_version != service.SCHEMA_VERSION or marker.app_mode != "demo":
            raise ValueError("existing provider environment marker is incompatible")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adopt-existing", action="store_true")
    args = parser.parse_args()
    if os.getenv("APP_MODE", "demo") != "demo":
        raise ValueError("provider migrations must target a demo environment")
    url = os.getenv("PROVIDER_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        raise ValueError("PROVIDER_DATABASE_URL must name the isolated provider database")
    factory = create_session_factory(url)
    from pathlib import Path
    config = Config(str(Path(__file__).resolve().parents[1] / "provider_alembic.ini"))
    inspector = inspect(factory.kw["bind"])
    existing = set(inspector.get_table_names())
    if "provider_alembic_version" not in existing and existing:
        if not args.adopt_existing:
            raise ValueError("existing unversioned provider schema requires explicit --adopt-existing validation")
        assert_existing_v1(factory)
        command.stamp(config, "0001_provider")
        print("Existing provider schema v1 verified and adopted; rows/passwords preserved")
    command.upgrade(config, "head")
    service.bootstrap_environment(factory, os.getenv("ENVIRONMENT_ID", "demo-local-1"))
    service.initialize(factory, os.getenv("ENVIRONMENT_ID", "demo-local-1"))
    print("Provider Alembic migration/environment verified")


if __name__ == "__main__":
    main()
