"""Allow bounded, explicitly requested IG gap revisits."""

from alembic import op

revision = "0004_ig_gap_revisit"
down_revision = "0003_ig_ingestion"
branch_labels = None
depends_on = None

_KIND_CONSTRAINT = "ck_md_ig_request_kind"
_ORIGINAL = (
    "kind IN ('incremental', 'recheck') AND "
    "(kind != 'recheck' OR policy_id IS NOT NULL)"
)


def upgrade() -> None:
    with op.batch_alter_table("market_data_ig_ingestion_attempts") as batch:
        batch.drop_constraint(_KIND_CONSTRAINT, type_="check")
        batch.create_check_constraint(
            _KIND_CONSTRAINT,
            "kind IN ('incremental', 'recheck', 'gap_revisit') AND "
            "(kind != 'recheck' OR policy_id IS NOT NULL) AND "
            "(kind != 'gap_revisit' OR (attempt_number = 1 AND point_limit = 1))",
        )


def downgrade() -> None:
    # Restoring the old constraint requires a journal without revisit attempts.
    # The database rolls back the downgrade if it would invalidate existing rows.
    with op.batch_alter_table("market_data_ig_ingestion_attempts") as batch:
        batch.drop_constraint(_KIND_CONSTRAINT, type_="check")
        batch.create_check_constraint(_KIND_CONSTRAINT, _ORIGINAL)
