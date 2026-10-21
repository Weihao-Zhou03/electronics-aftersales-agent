"""M0 identities, signed inbox, persistent fixed replies and conversation control."""
import sqlalchemy as sa

from alembic import op

revision = "0001_m0"
down_revision = None
branch_labels = None
depends_on = None


def pk():
    return sa.Column("id", sa.String(36), primary_key=True)


def dt(name, nullable=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def text(name, length, nullable=False):
    return sa.Column(name, sa.String(length), nullable=nullable)


def integer(name, nullable=False):
    return sa.Column(name, sa.Integer(), nullable=nullable)


def upgrade():
    op.create_table("users", pk(), text("username", 128), text("role", 16), text("password_hash", 255),
                    sa.Column("active", sa.Boolean(), nullable=False), sa.Column("is_demo", sa.Boolean(), nullable=False),
                    sa.UniqueConstraint("username"))
    op.create_table("sessions", pk(), sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
                    text("token_hash", 64), dt("expires_at"), dt("revoked_at", True), sa.UniqueConstraint("token_hash"))
    op.create_table("chat_bindings", pk(), text("instance_id", 128), integer("account_id"), integer("inbox_id"),
                    integer("contact_id"), text("customer_id", 36), text("auth_session_id", 36, True), text("source", 128),
                    dt("verified_at"), dt("expires_at"), dt("revoked_at", True),
                    sa.UniqueConstraint("instance_id", "account_id", "inbox_id", "contact_id", name="uq_chat_binding_scope"))
    op.create_table("conversations", pk(), text("instance_id", 128), integer("account_id"), integer("inbox_id"),
                    integer("external_id"), integer("contact_id", True), text("customer_id", 36, True),
                    text("control_mode", 32), integer("control_revision"), integer("input_revision"),
                    integer("last_processed_message_id", True), text("last_remote_status", 24, True),
                    text("lease_owner", 36, True), dt("lease_until", True), integer("fencing_revision"), dt("updated_at"),
                    sa.UniqueConstraint("instance_id", "account_id", "external_id", name="uq_conversation_scope"))
    op.create_table("webhook_inbox", pk(), text("event_key", 512), text("provider", 32), text("channel", 24),
                    text("event_type", 64), text("delivery_id", 255, True), text("payload_hash", 64),
                    sa.Column("payload", sa.JSON(), nullable=False), text("status", 32), dt("received_at"),
                    sa.UniqueConstraint("event_key"))
    op.create_table("jobs", pk(), text("kind", 32),
                    sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
                    sa.Column("event_id", sa.String(36), sa.ForeignKey("webhook_inbox.id"), nullable=True),
                    text("dedupe_key", 512), sa.Column("payload", sa.JSON(), nullable=False), text("status", 32),
                    integer("attempts"), dt("available_at"), text("lease_owner", 36, True), dt("lease_until", True),
                    integer("lease_version"), text("last_error", 256, True), sa.UniqueConstraint("dedupe_key"))
    op.create_index("ix_jobs_ready", "jobs", ["status", "available_at"])
    op.create_table("outbound_messages", pk(), text("intent_key", 255),
                    sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
                    sa.Column("content", sa.Text(), nullable=False), integer("control_revision"), integer("input_revision"),
                    integer("fencing_revision"), text("status", 32), integer("chatwoot_message_id", True),
                    text("last_error", 256, True), dt("created_at"), sa.UniqueConstraint("intent_key"))
    op.create_table("conversation_control_audit", pk(),
                    sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
                    text("actor_id", 128), text("action", 64), integer("revision"), dt("created_at"))


def downgrade():
    for table in ["conversation_control_audit", "outbound_messages", "jobs", "webhook_inbox", "conversations",
                  "chat_bindings", "sessions", "users"]:
        op.drop_table(table)
