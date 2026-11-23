"""Add the bounded IG H1 ingestion attempt journal."""

from alembic import op
from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
)

revision = "0003_ig_ingestion"
down_revision = "0002_market_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_data_ig_ingestion_attempts",
        Column("id", Text, primary_key=True),
        Column("job_id", Text, nullable=False),
        Column("attempt_number", Integer, nullable=False),
        Column(
            "product_id", Integer, ForeignKey("market_data_products.id"), nullable=False
        ),
        Column("policy_id", Integer, ForeignKey("market_data_timestamp_policies.id")),
        Column("resolution", Text, nullable=False),
        Column("kind", Text, nullable=False),
        Column("state", Text, nullable=False),
        Column("window_start", DateTime(timezone=True), nullable=False),
        Column("window_end", DateTime(timezone=True), nullable=False),
        Column("request_end", DateTime(timezone=True), nullable=False),
        Column("requested_at", DateTime(timezone=True), nullable=False),
        Column("point_limit", Integer, nullable=False),
        Column("received_at", DateTime(timezone=True)),
        Column("committed_at", DateTime(timezone=True)),
        Column("staged_response", JSON(none_as_null=True)),
        Column(
            "retrieval_id", Text, ForeignKey("market_data_retrievals.id"), unique=True
        ),
        Column("failure", Text),
        Column("retry_after", DateTime(timezone=True)),
        Column("outcome", JSON(none_as_null=True)),
        UniqueConstraint("job_id", "attempt_number", name="uq_md_ig_job_attempt"),
        CheckConstraint("window_end > window_start", name="ck_md_ig_window"),
        CheckConstraint("resolution = 'HOUR'", name="ck_md_ig_resolution"),
        CheckConstraint(
            "(attempt_number = 1 AND id = job_id) OR "
            "(attempt_number = 2 AND id != job_id)",
            name="ck_md_ig_job_identity",
        ),
        CheckConstraint(
            "(state IN ('staged', 'committed')) = (outcome IS NOT NULL) AND "
            "(retry_after IS NULL OR state = 'failed')",
            name="ck_md_ig_outcome",
        ),
        CheckConstraint(
            "request_end > window_start AND request_end <= window_end",
            name="ck_md_ig_request_end",
        ),
        CheckConstraint(
            "attempt_number IN (1, 2) AND point_limit BETWEEN 1 AND 25",
            name="ck_md_ig_attempt_budget",
        ),
        CheckConstraint(
            "kind IN ('incremental', 'recheck') AND "
            "(kind != 'recheck' OR policy_id IS NOT NULL)",
            name="ck_md_ig_request_kind",
        ),
        CheckConstraint(
            "(received_at IS NULL OR received_at >= requested_at) AND "
            "(committed_at IS NULL OR committed_at >= received_at)",
            name="ck_md_ig_attempt_times",
        ),
        CheckConstraint(
            "(state = 'requesting' AND received_at IS NULL AND staged_response IS NULL "
            "AND retrieval_id IS NULL AND failure IS NULL AND committed_at IS NULL) OR "
            "(state = 'staged' AND received_at IS NOT NULL AND staged_response IS NOT "
            "NULL AND retrieval_id IS NULL AND failure IS NULL "
            "AND committed_at IS NULL) OR "
            "(state = 'committed' AND received_at IS NOT NULL AND staged_response IS "
            "NULL AND retrieval_id = id AND retrieval_id IS NOT NULL AND failure IS "
            "NULL AND committed_at IS NOT NULL) OR "
            "(state = 'failed' AND received_at IS NULL AND staged_response IS NULL "
            "AND retrieval_id IS NULL AND failure IS NOT NULL "
            "AND committed_at IS NULL)",
            name="ck_md_ig_attempt_state",
        ),
    )
    op.create_index(
        "ix_md_ig_attempt_product",
        "market_data_ig_ingestion_attempts",
        ["product_id", "requested_at"],
    )

    if op.get_context().dialect.name == "postgresql":
        op.execute("""
            CREATE FUNCTION mrml_guard_ig_ingestion_attempt() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    IF NEW.state <> 'requesting' OR (
                        NEW.policy_id IS NOT NULL AND NOT EXISTS (
                            SELECT 1 FROM market_data_timestamp_policies p
                            JOIN market_data_mappings m ON p.mapping_id = m.id
                            WHERE p.id = NEW.policy_id AND m.product_id = NEW.product_id
                        )
                    ) OR (
                        NEW.attempt_number = 2 AND NOT EXISTS (
                            SELECT 1 FROM market_data_ig_ingestion_attempts a
                            WHERE a.id = NEW.job_id AND a.product_id = NEW.product_id
                              AND a.policy_id IS NOT DISTINCT FROM NEW.policy_id
                              AND a.state IN ('requesting', 'failed')
                              AND a.kind = NEW.kind
                              AND a.window_start <= NEW.window_start
                              AND a.window_end >= NEW.window_end
                              AND a.request_end >= NEW.request_end
                        )
                    ) THEN
                        RAISE EXCEPTION 'Invalid IG ingestion attempt'
                            USING ERRCODE = '23514';
                    END IF;
                ELSE
                    IF ROW(NEW.id, NEW.job_id, NEW.attempt_number, NEW.product_id,
                           NEW.policy_id, NEW.resolution, NEW.kind, NEW.window_start,
                           NEW.window_end, NEW.request_end, NEW.requested_at,
                           NEW.point_limit)
                       IS DISTINCT FROM
                       ROW(OLD.id, OLD.job_id, OLD.attempt_number, OLD.product_id,
                           OLD.policy_id, OLD.resolution, OLD.kind, OLD.window_start,
                           OLD.window_end, OLD.request_end, OLD.requested_at,
                           OLD.point_limit)
                       OR NOT (
                           (OLD.state = 'requesting'
                            AND NEW.state IN ('staged', 'failed'))
                           OR (OLD.state = 'staged' AND NEW.state = 'committed'
                               AND NEW.received_at = OLD.received_at)
                       ) THEN
                        RAISE EXCEPTION 'Invalid IG ingestion state transition'
                            USING ERRCODE = '23514';
                    END IF;
                    IF NEW.state = 'committed' AND NOT EXISTS (
                        SELECT 1 FROM market_data_retrievals r
                        WHERE r.id = NEW.retrieval_id AND r.product_id = NEW.product_id
                          AND r.resolution = NEW.resolution
                          AND r.retrieved_at = NEW.received_at
                    ) THEN
                        RAISE EXCEPTION 'IG attempt does not match its retrieval'
                            USING ERRCODE = '23503';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$
        """)
        op.execute("""
            CREATE TRIGGER mrml_ig_ingestion_attempt_guard
            BEFORE INSERT OR UPDATE ON market_data_ig_ingestion_attempts
            FOR EACH ROW EXECUTE FUNCTION mrml_guard_ig_ingestion_attempt()
        """)


def downgrade() -> None:
    op.drop_table("market_data_ig_ingestion_attempts")
    if op.get_context().dialect.name == "postgresql":
        op.execute("DROP FUNCTION mrml_guard_ig_ingestion_attempt()")
