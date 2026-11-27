"""Order immutable claims by account and freeze admission at the first claim."""

import sqlalchemy as sa
from alembic import context, op

revision = "0008_successive_claims"
down_revision = "0007_account_gate"
branch_labels = None
depends_on = None

_TABLE = "execution_claims"
_OLD_UNIQUE = "execution_claims_scope_id_key"
_NUMBER_UNIQUE = "uq_execution_claim_number"
_NUMBER_CHECK = "ck_execution_claim_number"
_SQLITE_NAMES = {"uq": "%(table_name)s_%(column_0_name)s_key"}


def upgrade() -> None:
    postgres = op.get_context().dialect.name == "postgresql"
    if postgres:
        op.execute("LOCK TABLE execution_accounts IN ACCESS EXCLUSIVE MODE")
        op.execute("LOCK TABLE execution_claims IN ACCESS EXCLUSIVE MODE")
        # Fail before assigning a history number if the old invariant was violated.
        op.execute("""
            DO $$ BEGIN
                IF EXISTS (
                    SELECT scope_id FROM execution_claims
                    GROUP BY scope_id HAVING count(*) > 1
                ) THEN
                    RAISE EXCEPTION 'Ambiguous version 0007 claim history';
                END IF;
            END $$
        """)
        op.execute("""
            CREATE FUNCTION mrml_require_claim_admission() RETURNS trigger
            LANGUAGE plpgsql AS $$
            DECLARE
                operation text;
            BEGIN
                EXECUTE format(
                    'SELECT operation FROM %I.execution_intents '
                    'WHERE scope_id = $1 AND id = $2', TG_TABLE_SCHEMA
                ) INTO operation USING NEW.scope_id, NEW.intent_id;
                IF current_setting('transaction_isolation') <> 'read committed'
                    OR NEW.claim_number IS DISTINCT FROM 1
                    OR operation IS DISTINCT FROM 'OPEN' THEN
                    RAISE EXCEPTION 'Account claim admission is unavailable'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $$
        """)
        op.execute("""
            CREATE TRIGGER broker_claim_successive_admission
            BEFORE INSERT ON execution_claims
            FOR EACH ROW EXECUTE FUNCTION mrml_require_claim_admission()
        """)
    with op.batch_alter_table(_TABLE, naming_convention=_SQLITE_NAMES) as batch:
        batch.add_column(
            sa.Column("claim_number", sa.Integer(), nullable=False, server_default="1")
        )
        batch.create_check_constraint(_NUMBER_CHECK, "claim_number > 0")
        batch.create_unique_constraint(_NUMBER_UNIQUE, ["scope_id", "claim_number"])
        batch.drop_constraint(_OLD_UNIQUE, type_="unique")


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("Successive claims downgrade requires an online check")
    connection = op.get_bind()
    postgres = connection.dialect.name == "postgresql"
    if postgres:
        op.execute("LOCK TABLE execution_accounts IN ACCESS EXCLUSIVE MODE")
        op.execute("LOCK TABLE execution_claims IN ACCESS EXCLUSIVE MODE")
    if (
        connection.scalar(
            sa.text("""
        SELECT 1 FROM execution_claims WHERE claim_number <> 1 LIMIT 1
    """)
        )
        is not None
    ):
        raise RuntimeError("Version 0007 cannot represent successive claim history")
    if postgres:
        op.execute("DROP TRIGGER broker_claim_successive_admission ON execution_claims")
        op.execute("DROP FUNCTION mrml_require_claim_admission()")
    with op.batch_alter_table(_TABLE, naming_convention=_SQLITE_NAMES) as batch:
        batch.create_unique_constraint(_OLD_UNIQUE, ["scope_id"])
        batch.drop_constraint(_NUMBER_UNIQUE, type_="unique")
        batch.drop_constraint(_NUMBER_CHECK, type_="check")
        batch.drop_column("claim_number")
