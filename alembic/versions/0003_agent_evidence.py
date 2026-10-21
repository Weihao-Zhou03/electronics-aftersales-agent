"""M2 private evidence and M3 agent/retrieval audit records."""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision = "0003_agent_evidence"
down_revision = "0002_business"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("system_settings", sa.Column("key", sa.String(128), primary_key=True), sa.Column("value", sa.JSON(), nullable=False))
    op.create_table("worker_heartbeats", sa.Column("name", sa.String(80), primary_key=True), sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False))
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table("evidence_attachments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("storage_key", sa.String(128), nullable=False, unique=True),
        sa.Column("mime", sa.String(64), nullable=False), sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("policy_chunks", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version_id", sa.String(36), sa.ForeignKey("policy_versions.id"), nullable=False),
        sa.Column("section_id", sa.String(128), nullable=False), sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(), nullable=True), sa.Column("embedding_model", sa.String(200), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.UniqueConstraint("version_id", "section_id", "embedding_model", name="uq_chunk_embedding"))
    op.create_table("agent_runs", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False), sa.Column("model", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False), sa.Column("status", sa.String(32), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False), sa.Column("duration_ms", sa.Integer()),
        sa.Column("error_kind", sa.String(64)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("agent_events", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False), sa.Column("tool_name", sa.String(128)),
        sa.Column("result", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    for name in ["agent_events", "agent_runs", "policy_chunks", "evidence_attachments", "worker_heartbeats", "system_settings"]:
        op.drop_table(name)
