"""Invalidate only unconfirmed agent drafts when conversation ownership stops."""
import sqlalchemy as sa

from alembic import op

revision = "0004_agent_draft_revocation"
down_revision = "0003_agent_evidence"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("proposals", sa.Column("agent_conversation_id", sa.String(36), nullable=True))
    op.add_column("proposals", sa.Column("agent_control_revision", sa.Integer(), nullable=True))
    op.add_column("proposals", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("proposals") as batch:
        batch.create_foreign_key("fk_proposal_agent_conversation", "conversations", ["agent_conversation_id"], ["id"])


def downgrade():
    with op.batch_alter_table("proposals") as batch:
        batch.drop_constraint("fk_proposal_agent_conversation", type_="foreignkey")
        batch.drop_column("revoked_at")
        batch.drop_column("agent_control_revision")
        batch.drop_column("agent_conversation_id")
