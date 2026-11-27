"""Add auditable account-gate transitions."""

import sqlalchemy as sa
from alembic import context, op

revision = "0007_account_gate"
down_revision = "0006_broker_observations"
branch_labels = None
depends_on = None

_TABLE = "broker_gate_transitions"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "scope_id",
            sa.Uuid(),
            sa.ForeignKey("execution_accounts.id"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("previous_revision", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("before_blockers", sa.JSON(), nullable=False),
        sa.Column("after_blockers", sa.JSON(), nullable=False),
        sa.Column("claim_id", sa.Uuid(), sa.ForeignKey("execution_claims.id")),
        sa.Column("intent_id", sa.Uuid()),
        sa.Column("assessment_id", sa.Uuid()),
        sa.Column("positions_id", sa.Uuid()),
        sa.Column("orders_id", sa.Uuid()),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("policy", sa.String(32), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scope_id", "revision"),
        sa.ForeignKeyConstraint(
            ["scope_id", "intent_id", "assessment_id"],
            [
                "broker_assessments.scope_id",
                "broker_assessments.intent_id",
                "broker_assessments.id",
            ],
        ),
        sa.CheckConstraint(
            "previous_revision >= 0 AND revision = previous_revision + 1",
            name="ck_gate_transition_revision",
        ),
        sa.CheckConstraint(
            "(reason = 'OPERATIONAL_RESTRICTION' AND claim_id IS NULL "
            "AND intent_id IS NULL AND assessment_id IS NULL "
            "AND positions_id IS NULL AND orders_id IS NULL) OR "
            "(reason IN ('OPENING_RESOLVED', 'REJECTION_RESOLVED') "
            "AND claim_id IS NOT NULL "
            "AND intent_id IS NOT NULL AND assessment_id IS NOT NULL "
            "AND positions_id IS NOT NULL AND orders_id IS NOT NULL)",
            name="ck_gate_transition_reason",
        ),
    )
    if op.get_context().dialect.name == "postgresql":
        op.execute("""
            CREATE FUNCTION mrml_check_gate_transition() RETURNS trigger
            LANGUAGE plpgsql AS $$
            DECLARE
                prior_revision integer;
                prior_decision timestamptz;
                valid_link boolean;
            BEGIN
                EXECUTE format(
                    'SELECT id FROM %I.execution_accounts WHERE id = $1 FOR UPDATE',
                    TG_TABLE_SCHEMA
                ) USING NEW.scope_id;
                EXECUTE format(
                    'SELECT revision, decided_at FROM %I.broker_gate_transitions '
                    'WHERE scope_id = $1 ORDER BY revision DESC LIMIT 1',
                    TG_TABLE_SCHEMA
                ) INTO prior_revision, prior_decision USING NEW.scope_id;
                IF NEW.previous_revision <> coalesce(prior_revision, 0)
                    OR NEW.decided_at < prior_decision
                    OR NEW.policy <> 'account-gate-v1'
                    OR json_typeof(NEW.before_blockers) IS DISTINCT FROM 'array'
                    OR json_typeof(NEW.after_blockers) IS DISTINCT FROM 'array' THEN
                    RAISE EXCEPTION 'Invalid account gate revision or policy'
                        USING ERRCODE = '23514';
                END IF;
                IF EXISTS (
                    SELECT 1 FROM json_array_elements(
                        (NEW.before_blockers::jsonb || NEW.after_blockers::jsonb)::json
                    ) AS blockers(value)
                    WHERE json_typeof(value) <> 'string'
                        OR (value::jsonb #>> '{}') NOT IN (
                        'BROKER_IDENTITY_UNVERIFIED', 'EXPOSURE_UNVERIFIED',
                        'EXPOSURE_STALE', 'CLAIM_UNRESOLVED', 'CONFIRMATION_MISSING',
                        'EVIDENCE_CONFLICT', 'EVIDENCE_UNREPLAYABLE',
                        'UNSUPPORTED_OUTCOME', 'KNOWN_POSITION',
                        'UNATTRIBUTED_POSITION',
                        'CONTINGENT_ORDER', 'OPERATIONAL_RESTRICTION'
                    )
                ) THEN
                    RAISE EXCEPTION 'Invalid account gate blocker snapshot'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.claim_id IS NOT NULL THEN
                    EXECUTE format(
                        'SELECT true FROM %I.execution_claims c '
                        'JOIN %I.broker_assessments a '
                        'ON a.scope_id = c.scope_id AND a.intent_id = c.intent_id '
                        'WHERE c.id = $1 AND c.scope_id = $2 AND c.intent_id = $3 '
                        'AND a.id = $4 AND a.positions_id = $5 AND a.orders_id = $6 '
                        'AND a.evaluated_at >= c.claimed_at AND a.evaluated_at <= $7 '
                        'AND a.status = ''MATCHED'' AND a.algorithm = ''opening-v1'' '
                        'AND a.reason = $8',
                        TG_TABLE_SCHEMA, TG_TABLE_SCHEMA
                    ) INTO valid_link USING NEW.claim_id, NEW.scope_id, NEW.intent_id,
                        NEW.assessment_id, NEW.positions_id, NEW.orders_id,
                        NEW.decided_at, CASE NEW.reason WHEN 'OPENING_RESOLVED'
                        THEN 'OPEN_POSITION_MATCHED'
                        ELSE 'REJECTION_WITHOUT_ATTRIBUTABLE_EXPOSURE' END;
                    IF valid_link IS DISTINCT FROM true THEN
                        RAISE EXCEPTION 'Invalid account gate evidence association'
                            USING ERRCODE = '23514';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$
        """)
        op.execute("""
            CREATE TRIGGER broker_gate_revision BEFORE INSERT ON broker_gate_transitions
            FOR EACH ROW EXECUTE FUNCTION mrml_check_gate_transition()
        """)
        op.execute("""
            CREATE TRIGGER broker_gate_immutable
            BEFORE UPDATE OR DELETE OR TRUNCATE ON broker_gate_transitions
            FOR EACH STATEMENT EXECUTE FUNCTION mrml_execution_immutable()
        """)
        op.execute("""
            CREATE FUNCTION mrml_check_claim_gate() RETURNS trigger
            LANGUAGE plpgsql AS $$
            DECLARE
                held boolean;
            BEGIN
                EXECUTE format(
                    'SELECT id FROM %I.execution_accounts WHERE id = $1 FOR UPDATE',
                    TG_TABLE_SCHEMA
                ) USING NEW.scope_id;
                EXECUTE format(
                    'SELECT EXISTS (SELECT 1 FROM %I.broker_gate_transitions '
                    'WHERE scope_id = $1 AND reason = ''OPERATIONAL_RESTRICTION'')',
                    TG_TABLE_SCHEMA
                ) INTO held USING NEW.scope_id;
                IF held THEN
                    RAISE EXCEPTION 'Account gate blocks claim insertion'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $$
        """)
        op.execute("""
            CREATE TRIGGER broker_claim_gate BEFORE INSERT ON execution_claims
            FOR EACH ROW EXECUTE FUNCTION mrml_check_claim_gate()
        """)


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("Account gate downgrade requires an online check")
    connection = op.get_bind()
    postgres = connection.dialect.name == "postgresql"
    if postgres:
        op.execute("LOCK TABLE execution_accounts IN ACCESS EXCLUSIVE MODE")
        op.execute(f"LOCK TABLE execution_claims, {_TABLE} IN ACCESS EXCLUSIVE MODE")
    if connection.scalar(sa.text(f"SELECT 1 FROM {_TABLE} LIMIT 1")) is not None:
        raise RuntimeError("Account gate downgrade requires empty gate history")
    if postgres:
        op.execute("DROP TRIGGER broker_claim_gate ON execution_claims")
        op.execute("DROP FUNCTION mrml_check_claim_gate()")
    op.drop_table(_TABLE)
    if postgres:
        op.execute("DROP FUNCTION mrml_check_gate_transition()")
