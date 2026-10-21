"""Persistent independent simulated fulfillment and delivery outbox."""
import sqlalchemy as sa

from alembic import op

revision = "0001_provider"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("provider_environment", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("app_mode", sa.String(16), nullable=False), sa.Column("environment_id", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False))
    op.create_table("provider_operations", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("idempotency_key", sa.String(200), nullable=False, unique=True),
        sa.Column("kind", sa.String(16), nullable=False), sa.Column("environment_id", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False), sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False), sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('refund','shipment')", name="ck_provider_kind"))
    op.create_table("provider_faults", sa.Column("idempotency_key", sa.String(200), primary_key=True),
                    sa.Column("mode", sa.String(32), nullable=False))
    op.create_table("provider_callbacks", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("operation_id", sa.String(36), nullable=False), sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True), sa.Column("last_error", sa.Text(), nullable=True))
    op.create_index("ix_provider_callbacks_operation_id", "provider_callbacks", ["operation_id"])


def downgrade():
    op.drop_index("ix_provider_callbacks_operation_id", table_name="provider_callbacks")
    op.drop_table("provider_callbacks")
    op.drop_table("provider_faults")
    op.drop_table("provider_operations")
    op.drop_table("provider_environment")
