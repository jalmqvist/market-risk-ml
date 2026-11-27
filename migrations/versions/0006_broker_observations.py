"""Persist immutable observations and reconciliation assessments."""

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "0006_broker_observations"
down_revision = "0005_execution_journal"
branch_labels = None
depends_on = None

_TABLES = ("broker_observations", "broker_assessments", "broker_assessment_inputs")


def upgrade() -> None:
    op.create_table(
        "broker_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=True),
        sa.Column("original_id", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collection_status", sa.String(length=16), nullable=True),
        sa.Column("target_deal_id", sa.String(length=128), nullable=True),
        sa.Column("member_count", sa.Integer(), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind IN ('POSITIONS', 'WORKING_ORDERS') AND intent_id IS NULL "
            "AND collection_status IS NOT NULL "
            "AND collection_status IN ('COMPLETE', 'INCOMPLETE', "
            "'FAILED', 'NOT_FOUND') "
            "AND member_count IS NOT NULL AND member_count BETWEEN 0 AND 100) OR "
            "(kind IN ('ACKNOWLEDGEMENT', 'CONFIRMATION', 'ACTIVITY_OPENING', "
            "'CONFIRMATION_NOT_FOUND') AND intent_id IS NOT NULL "
            "AND collection_status IS NULL AND member_count IS NULL "
            "AND target_deal_id IS NULL AND started_at IS NULL)",
            name="ck_broker_observation_shape",
        ),
        sa.CheckConstraint(
            "(target_deal_id IS NULL OR (kind = 'POSITIONS' AND member_count <= 1)) "
            "AND (collection_status <> 'NOT_FOUND' OR target_deal_id IS NOT NULL) "
            "AND (target_deal_id IS NULL OR collection_status <> 'COMPLETE' "
            "OR member_count = 1) "
            "AND (collection_status NOT IN ('FAILED', 'NOT_FOUND') "
            "OR member_count = 0)",
            name="ck_broker_collection_coverage",
        ),
        sa.CheckConstraint(
            "((started_at IS NULL AND ended_at IS NULL) OR "
            "(started_at IS NOT NULL AND ended_at IS NOT NULL "
            "AND started_at <= ended_at AND ended_at <= observed_at))",
            name="ck_broker_collection_interval",
        ),
        sa.CheckConstraint("schema_version = 1", name="ck_broker_observation_version"),
        sa.ForeignKeyConstraint(
            ["scope_id", "intent_id"],
            ["execution_intents.scope_id", "execution_intents.id"],
        ),
        sa.ForeignKeyConstraint(
            ["scope_id"],
            ["execution_accounts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_id", "id", "kind"),
        sa.UniqueConstraint("scope_id", "intent_id", "id"),
        sa.UniqueConstraint("scope_id", "original_id"),
    )
    op.create_table(
        "broker_assessments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=False),
        sa.Column("positions_id", sa.Uuid(), nullable=False),
        sa.Column("orders_id", sa.Uuid(), nullable=False),
        sa.Column(
            "positions_kind",
            sa.String(length=24),
            server_default="POSITIONS",
            nullable=False,
        ),
        sa.Column(
            "orders_kind",
            sa.String(length=24),
            server_default="WORKING_ORDERS",
            nullable=False,
        ),
        sa.Column("transaction_count", sa.Integer(), nullable=False),
        sa.Column("algorithm", sa.String(length=64), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_age_us", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("confirmation_outcome", sa.String(length=16), nullable=True),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "positions_kind = 'POSITIONS' AND orders_kind = 'WORKING_ORDERS'",
            name="ck_broker_assessment_collection_kinds",
        ),
        sa.CheckConstraint(
            "status IN ('MATCHED', 'MISMATCH', 'UNRESOLVED')",
            name="ck_broker_assessment_result",
        ),
        sa.CheckConstraint(
            "max_age_us > 0 AND transaction_count BETWEEN 0 AND 100",
            name="ck_broker_assessment_parameters",
        ),
        sa.ForeignKeyConstraint(
            ["scope_id", "intent_id"],
            ["execution_intents.scope_id", "execution_intents.id"],
        ),
        sa.ForeignKeyConstraint(
            ["scope_id", "orders_id", "orders_kind"],
            [
                "broker_observations.scope_id",
                "broker_observations.id",
                "broker_observations.kind",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["scope_id", "positions_id", "positions_kind"],
            [
                "broker_observations.scope_id",
                "broker_observations.id",
                "broker_observations.kind",
            ],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_id", "intent_id", "id"),
    )
    op.create_table(
        "broker_assessment_inputs",
        sa.Column("assessment_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=False),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("ordinal BETWEEN 0 AND 99", name="ck_broker_input_ordinal"),
        sa.ForeignKeyConstraint(
            ["scope_id", "intent_id", "assessment_id"],
            [
                "broker_assessments.scope_id",
                "broker_assessments.intent_id",
                "broker_assessments.id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["scope_id", "intent_id", "observation_id"],
            [
                "broker_observations.scope_id",
                "broker_observations.intent_id",
                "broker_observations.id",
            ],
        ),
        sa.PrimaryKeyConstraint("assessment_id", "ordinal"),
        sa.UniqueConstraint("assessment_id", "observation_id"),
    )

    if op.get_context().dialect.name == "postgresql":
        op.create_check_constraint(
            "ck_broker_observation_document",
            "broker_observations",
            "CASE WHEN collection_status IS NULL THEN jsonb_typeof(payload) = 'object' "
            "WHEN jsonb_typeof(payload) = 'array' "
            "THEN jsonb_array_length(payload) = member_count ELSE false END",
        )
        for table in _TABLES:
            op.execute(
                f"CREATE TRIGGER broker_immutable "
                f"BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} "
                "FOR EACH STATEMENT EXECUTE FUNCTION mrml_execution_immutable()"
            )
        op.execute("""
            CREATE FUNCTION mrml_check_broker_assessment_manifest() RETURNS trigger
            LANGUAGE plpgsql AS $$
            DECLARE
                assessment uuid;
                expected integer;
                actual bigint;
                last_ordinal integer;
            BEGIN
                IF TG_TABLE_NAME = 'broker_assessments' THEN
                    assessment := NEW.id;
                ELSE
                    assessment := NEW.assessment_id;
                END IF;
                EXECUTE format(
                    'SELECT transaction_count FROM %I.broker_assessments WHERE id = $1',
                    TG_TABLE_SCHEMA
                ) INTO expected USING assessment;
                EXECUTE format(
                    'SELECT count(*), coalesce(max(ordinal), -1) '
                    'FROM %I.broker_assessment_inputs WHERE assessment_id = $1',
                    TG_TABLE_SCHEMA
                ) INTO actual, last_ordinal USING assessment;
                IF expected IS NULL OR actual <> expected
                    OR last_ordinal <> expected - 1 THEN
                    RAISE EXCEPTION 'Assessment input manifest is incomplete or sealed'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NULL;
            END;
            $$
        """)
        for table in ("broker_assessments", "broker_assessment_inputs"):
            op.execute(
                f"CREATE CONSTRAINT TRIGGER broker_assessment_manifest "
                f"AFTER INSERT ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                "EXECUTE FUNCTION mrml_check_broker_assessment_manifest()"
            )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("Broker observations downgrade requires an online check")
    connection = op.get_bind()
    is_postgres = connection.dialect.name == "postgresql"
    if is_postgres:
        tables = ", ".join(_TABLES)
        op.execute(f"LOCK TABLE {tables} IN ACCESS EXCLUSIVE MODE")
    for table in _TABLES:
        if connection.scalar(sa.text(f"SELECT 1 FROM {table} LIMIT 1")) is not None:
            raise RuntimeError("Broker observations downgrade requires empty tables")

    op.drop_table("broker_assessment_inputs")
    op.drop_table("broker_assessments")
    op.drop_table("broker_observations")
    if is_postgres:
        op.execute("DROP FUNCTION mrml_check_broker_assessment_manifest()")
