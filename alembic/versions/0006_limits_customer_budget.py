"""Persistent request limits and per-customer model budget scope."""

import sqlalchemy as sa

from alembic import op

revision = "0006_limits_budget"
down_revision = "0005_outbound_reconciliation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("rate_limits", sa.Column("id", sa.String(64), primary_key=True),
                    sa.Column("count", sa.Integer(), nullable=False),
                    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("customer_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_agent_run_customer", "users", ["customer_id"], ["id"])


def downgrade():
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint("fk_agent_run_customer", type_="foreignkey")
        batch.drop_column("customer_id")
    op.drop_table("rate_limits")
