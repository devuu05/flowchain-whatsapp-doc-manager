"""sender roles (WhatsApp admins) and guided conversation state

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "authorized_senders",
        sa.Column("role", sa.String(length=20), server_default="member", nullable=False),
    )
    op.create_table(
        "conversation_states",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column("sender", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["integration_configs.id"],
            name=op.f("fk_conversation_states_connection_id_integration_configs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_conversation_states_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_states")),
        sa.UniqueConstraint(
            "tenant_id",
            "connection_id",
            "sender",
            name=op.f("uq_conversation_states_tenant_id_connection_id_sender"),
        ),
    )
    op.create_index(
        op.f("ix_conversation_states_tenant_id"), "conversation_states", ["tenant_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_conversation_states_tenant_id"), table_name="conversation_states")
    op.drop_table("conversation_states")
    op.drop_column("authorized_senders", "role")
