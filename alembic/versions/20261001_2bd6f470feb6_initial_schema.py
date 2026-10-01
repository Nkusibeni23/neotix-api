"""initial schema

Revision ID: 2bd6f470feb6
Revises:
Create Date: 2026-10-01 21:28:18.228089

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2bd6f470feb6"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema.

    Enum-like columns are VARCHAR + CHECK (see app.models._enum).
    """
    op.create_table(
        "episodes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.String(length=64), nullable=False),
        sa.Column("robot_id", sa.String(length=64), nullable=False),
        sa.Column("task_name", sa.String(length=255), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("operator_name", sa.String(length=255), nullable=False),
        sa.Column("quality", sa.String(length=20), nullable=False),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("quality IN ('good', 'usable', 'bad')", name="episode_quality"),
        sa.CheckConstraint("duration_seconds > 0", name="duration_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("episode_id"),
    )
    op.create_index(
        "ix_episodes_recorded_at_robot", "episodes", ["recorded_at", "robot_id"], unique=False
    )
    op.create_index("ix_episodes_task_quality", "episodes", ["task_name", "quality"], unique=False)
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("organisation", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('client', 'operator', 'admin')", name="user_role"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("task_name", sa.String(length=255), nullable=False),
        sa.Column("episodes_requested", sa.Integer(), nullable=False),
        sa.Column("deadline", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
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
        sa.CheckConstraint(
            "status IN ('submitted', 'in_progress', 'delivered', 'accepted', 'rejected')",
            name="request_status",
        ),
        sa.CheckConstraint("episodes_requested > 0", name="episodes_requested_positive"),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_requests_client_id"), "requests", ["client_id"], unique=False)
    op.create_index(op.f("ix_requests_status"), "requests", ["status"], unique=False)
    op.create_table(
        "assignments",
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("assigned_by_id", sa.Integer(), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["request_id"], ["requests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("episode_id"),
    )
    op.create_index(op.f("ix_assignments_request_id"), "assignments", ["request_id"], unique=False)
    op.create_table(
        "request_status_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("changed_by_id", sa.Integer(), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "from_status IN ('submitted', 'in_progress', 'delivered', 'accepted', 'rejected')",
            name="event_from",
        ),
        sa.CheckConstraint(
            "to_status IN ('submitted', 'in_progress', 'delivered', 'accepted', 'rejected')",
            name="event_to",
        ),
        sa.ForeignKeyConstraint(
            ["changed_by_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(["request_id"], ["requests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_status_events_request_changed",
        "request_status_events",
        ["request_id", "changed_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_status_events_request_changed", table_name="request_status_events")
    op.drop_table("request_status_events")
    op.drop_index(op.f("ix_assignments_request_id"), table_name="assignments")
    op.drop_table("assignments")
    op.drop_index(op.f("ix_requests_status"), table_name="requests")
    op.drop_index(op.f("ix_requests_client_id"), table_name="requests")
    op.drop_table("requests")
    op.drop_table("users")
    op.drop_index("ix_episodes_task_quality", table_name="episodes")
    op.drop_index("ix_episodes_recorded_at_robot", table_name="episodes")
    op.drop_table("episodes")
