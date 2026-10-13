from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).parents[1]


def test_downgrade_drops_only_operational_journal_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "journal.sqlite"
    database_url = f"sqlite:///{database_path}"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)"))

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    table_names = set(inspect(engine).get_table_names())
    assert "unrelated" in table_names
    assert (
        not {
            "config_snapshots",
            "fills",
            "orders",
            "risk_decisions",
            "signals",
            "runs",
        }
        & table_names
    )
    engine.dispose()
