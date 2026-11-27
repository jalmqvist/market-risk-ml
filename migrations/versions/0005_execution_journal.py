"""Add durable execution journal."""

import sqlalchemy as sa
from alembic import context, op

revision = "0005_execution_journal"
down_revision = "0004_ig_gap_revisit"
branch_labels = None
depends_on = None

_TABLES = (
    "execution_claims",
    "execution_evidence",
    "execution_intents",
    "execution_accounts",
)


def upgrade() -> None:
    op.create_table(
        "execution_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("account_id", sa.String(length=128), nullable=False),
        sa.CheckConstraint("environment = 'demo'", name="ck_execution_demo"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "environment", "account_id"),
    )
    op.create_table(
        "execution_intents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("request_reference", sa.String(length=30), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("level", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("stop_level", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("stop_distance", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("limit_level", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("limit_distance", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("authorization_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "operation IN ('OPEN', 'CLOSE')", name="ck_execution_operation"
        ),
        sa.CheckConstraint("quantity > 0", name="ck_execution_positive_quantity"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["scope_id"],
            ["execution_accounts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_id", "id"),
        sa.UniqueConstraint("scope_id", "idempotency_key"),
        sa.UniqueConstraint("scope_id", "request_reference"),
    )
    op.create_table(
        "execution_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=False),
        sa.Column("deduplication_key", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("broker_id", sa.String(length=128), nullable=True),
        sa.Column("broker_role", sa.String(length=24), nullable=True),
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("price", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "broker_role IN ('RETURNED_REFERENCE', 'TRANSACTION_DEAL', "
            "'AFFECTED_DEAL', 'POSITION', 'WORKING_ORDER')",
            name="ck_execution_broker_role",
        ),
        sa.CheckConstraint(
            "kind IN ('DISPATCH_CLAIM', 'ACKNOWLEDGEMENT', 'CONFIRMATION', "
            "'EXPOSURE', 'ADMIN_REVIEW', 'CONFLICT')",
            name="ck_execution_evidence_kind",
        ),
        sa.CheckConstraint(
            "(kind = 'ADMIN_REVIEW') = (actor_id IS NOT NULL)",
            name="ck_execution_review_actor",
        ),
        sa.CheckConstraint(
            "(broker_id IS NULL) = (broker_role IS NULL)",
            name="ck_execution_evidence_identity",
        ),
        sa.ForeignKeyConstraint(
            ["scope_id", "intent_id"],
            ["execution_intents.scope_id", "execution_intents.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_id", "deduplication_key"),
        sa.UniqueConstraint("scope_id", "intent_id", "id"),
        sa.UniqueConstraint("scope_id", "intent_id", "id", "kind"),
    )
    op.create_table(
        "execution_claims",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=False),
        sa.Column(
            "event_kind", sa.String(24), nullable=False, server_default="DISPATCH_CLAIM"
        ),
        sa.CheckConstraint(
            "event_kind = 'DISPATCH_CLAIM'", name="ck_execution_claim_kind"
        ),
        sa.Column("baseline_id", sa.Uuid(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["scope_id", "intent_id", "baseline_id"],
            [
                "execution_evidence.scope_id",
                "execution_evidence.intent_id",
                "execution_evidence.id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["scope_id", "intent_id", "id", "event_kind"],
            [
                "execution_evidence.scope_id",
                "execution_evidence.intent_id",
                "execution_evidence.id",
                "execution_evidence.kind",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["scope_id", "intent_id"],
            ["execution_intents.scope_id", "execution_intents.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("intent_id"),
        sa.UniqueConstraint("scope_id"),
    )

    if op.get_context().dialect.name == "postgresql":
        op.execute("""
            CREATE FUNCTION mrml_execution_immutable() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'Execution journal rows are immutable'
                    USING ERRCODE = '55000';
            END;
            $$
        """)
        for table in _TABLES:
            op.execute(
                f"CREATE TRIGGER execution_immutable "
                f"BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} "
                f"FOR EACH STATEMENT EXECUTE FUNCTION mrml_execution_immutable()"
            )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "Execution journal downgrade requires an online emptiness check"
        )
    connection = op.get_bind()
    is_postgres = connection.dialect.name == "postgresql"
    if is_postgres:
        op.execute(
            f"LOCK TABLE {', '.join(reversed(_TABLES))} IN ACCESS EXCLUSIVE MODE"
        )
    for table in _TABLES:
        if connection.scalar(sa.text(f"SELECT 1 FROM {table} LIMIT 1")) is not None:
            raise RuntimeError(
                "Execution journal downgrade requires empty execution tables"
            )

    op.drop_table("execution_claims")
    op.drop_table("execution_evidence")
    op.drop_table("execution_intents")
    op.drop_table("execution_accounts")
    if is_postgres:
        op.execute("DROP FUNCTION mrml_execution_immutable()")
