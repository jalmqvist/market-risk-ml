"""Add source observations and canonical H1 interpretations."""

import sqlalchemy as sa
from alembic import op

revision = "0002_market_data"
down_revision = "0001_operational_journal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    price = sa.Numeric().with_variant(sa.Text(), "sqlite")
    op.create_table(
        "market_data_products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("instrument_id", sa.Text(), nullable=False),
        sa.Column("variant", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "instrument_id", "variant", name="uq_md_product_identity"
        ),
    )
    op.create_table(
        "market_data_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("instrument", sa.Text(), nullable=False),
        sa.Column("provenance", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["market_data_products.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "version", name="uq_md_mapping_version"),
    )
    op.create_table(
        "market_data_retrievals",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=False),
        sa.Column("timestamp_field", sa.Text(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_metadata", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["market_data_products.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_md_retrieval_product_history",
        "market_data_retrievals",
        ["product_id", "resolution", "retrieved_at"],
        unique=False,
    )
    op.create_table(
        "market_data_observations",
        sa.Column("retrieval_id", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("raw_timestamp", sa.Text(), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("bid_open", price, nullable=False),
        sa.Column("bid_high", price, nullable=False),
        sa.Column("bid_low", price, nullable=False),
        sa.Column("bid_close", price, nullable=False),
        sa.Column("ask_open", price, nullable=False),
        sa.Column("ask_high", price, nullable=False),
        sa.Column("ask_low", price, nullable=False),
        sa.Column("ask_close", price, nullable=False),
        sa.CheckConstraint("ordinal >= 0", name="ck_md_observation_ordinal"),
        sa.ForeignKeyConstraint(
            ["retrieval_id"],
            ["market_data_retrievals.id"],
        ),
        sa.PrimaryKeyConstraint("retrieval_id", "ordinal"),
    )
    op.create_index(
        "ix_md_observation_source_label",
        "market_data_observations",
        ["source_timestamp", "retrieval_id"],
        unique=False,
    )
    op.create_table(
        "market_data_timestamp_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mapping_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("meaning", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("approval", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "meaning IN ('INTERVAL_START', 'INTERVAL_END')", name="ck_md_policy_meaning"
        ),
        sa.ForeignKeyConstraint(
            ["mapping_id"],
            ["market_data_mappings.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mapping_id", "name", name="uq_md_policy_name"),
    )
    op.create_table(
        "market_data_h1_interpretations",
        sa.Column("retrieval_id", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=False),
        sa.Column("transformation_version", sa.Text(), nullable=False),
        sa.Column("interpreted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completion_state", sa.Text(), nullable=False),
        sa.Column("completion_evidence", sa.Text(), nullable=True),
        sa.Column("first_available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("availability_evidence", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "completion_state = 'UNKNOWN' OR completion_evidence IS NOT NULL",
            name="ck_md_h1_completion_evidence",
        ),
        sa.CheckConstraint(
            "completion_state IN ('COMPLETE', 'INCOMPLETE', 'UNKNOWN')",
            name="ck_md_h1_completion_state",
        ),
        sa.CheckConstraint('"end" > start', name="ck_md_h1_interval"),
        sa.CheckConstraint(
            "first_available_at IS NULL OR availability_evidence IS NOT NULL",
            name="ck_md_h1_availability_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["market_data_timestamp_policies.id"],
        ),
        sa.ForeignKeyConstraint(
            ["retrieval_id", "ordinal"],
            [
                "market_data_observations.retrieval_id",
                "market_data_observations.ordinal",
            ],
            name="fk_md_h1_observation",
        ),
        sa.PrimaryKeyConstraint(
            "retrieval_id", "ordinal", "policy_id", "transformation_version"
        ),
    )
    op.create_index(
        "ix_md_h1_history",
        "market_data_h1_interpretations",
        ["policy_id", "transformation_version", "start"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_md_h1_history", table_name="market_data_h1_interpretations")
    op.drop_table("market_data_h1_interpretations")
    op.drop_table("market_data_timestamp_policies")
    op.drop_index(
        "ix_md_observation_source_label", table_name="market_data_observations"
    )
    op.drop_table("market_data_observations")
    op.drop_index(
        "ix_md_retrieval_product_history", table_name="market_data_retrievals"
    )
    op.drop_table("market_data_retrievals")
    op.drop_table("market_data_mappings")
    op.drop_table("market_data_products")
