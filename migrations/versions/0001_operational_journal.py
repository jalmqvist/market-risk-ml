"""Create the v0 operational journal tables."""

from __future__ import annotations

from alembic import op

from mrml.journal.models import Base

revision = "0001_operational_journal"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


    op.drop_table("config_snapshots")
    op.drop_table("fills")
    op.drop_table("orders")
    op.drop_table("risk_decisions")
    op.drop_table("signals")
    op.drop_table("runs")
