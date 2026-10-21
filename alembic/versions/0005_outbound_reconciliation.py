"""Immutable staff evidence resolving unknown chat delivery without resending it."""
import sqlalchemy as sa

from alembic import op

revision = "0005_outbound_reconciliation"
down_revision = "0004_agent_draft_revocation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("outbound_reconciliations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("outbound_id", sa.String(36), sa.ForeignKey("outbound_messages.id"), nullable=False, unique=True),
        sa.Column("actor_id", sa.String(36), nullable=False),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("control_revision", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE TRIGGER deny_outbound_audit_mutation BEFORE UPDATE OR DELETE ON outbound_reconciliations FOR EACH ROW EXECUTE FUNCTION aftersales_deny_published_mutation()")


def downgrade():
    op.drop_table("outbound_reconciliations")
