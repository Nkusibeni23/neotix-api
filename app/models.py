from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Role(StrEnum):
    client = "client"
    operator = "operator"
    admin = "admin"


class Quality(StrEnum):
    good = "good"
    usable = "usable"
    bad = "bad"


class RequestStatus(StrEnum):
    submitted = "submitted"
    in_progress = "in_progress"
    delivered = "delivered"
    accepted = "accepted"
    rejected = "rejected"


def _enum(cls: type[StrEnum], name: str) -> Enum:
    # Stored as VARCHAR + CHECK constraint rather than a native Postgres enum: adding a value
    # later is a one-line migration instead of ALTER TYPE.
    return Enum(
        cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda e: [m.value for m in e],
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)  # stored lower-cased
    name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(_enum(Role, "user_role"))
    organisation: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (
        CheckConstraint("duration_seconds > 0", name="duration_positive"),
        # Analytics: per-day/per-robot counts over a date range.
        Index("ix_episodes_recorded_at_robot", "recorded_at", "robot_id"),
        # Operator episode picker filters by task and quality.
        Index("ix_episodes_task_quality", "task_name", "quality"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Natural key from the recording system. UNIQUE is what makes re-imports idempotent.
    episode_id: Mapped[str] = mapped_column(String(64), unique=True)
    robot_id: Mapped[str] = mapped_column(String(64))
    task_name: Mapped[str] = mapped_column(String(255))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int] = mapped_column(Integer)
    operator_name: Mapped[str] = mapped_column(String(255))
    quality: Mapped[Quality] = mapped_column(_enum(Quality, "episode_quality"))
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    assignment: Mapped["Assignment | None"] = relationship(back_populates="episode")


class DatasetRequest(Base):
    __tablename__ = "requests"
    __table_args__ = (
        CheckConstraint("episodes_requested > 0", name="episodes_requested_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    task_name: Mapped[str] = mapped_column(String(255))
    episodes_requested: Mapped[int] = mapped_column(Integer)
    deadline: Mapped[date] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[RequestStatus] = mapped_column(
        _enum(RequestStatus, "request_status"), default=RequestStatus.submitted, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    client: Mapped[User] = relationship()
    events: Mapped[list["RequestStatusEvent"]] = relationship(
        back_populates="request", order_by="RequestStatusEvent.changed_at"
    )
    assignments: Mapped[list["Assignment"]] = relationship(back_populates="request")


class RequestStatusEvent(Base):
    """Append-only audit trail: one row per status change (including creation)."""

    __tablename__ = "request_status_events"
    __table_args__ = (Index("ix_status_events_request_changed", "request_id", "changed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"))
    from_status: Mapped[RequestStatus | None] = mapped_column(_enum(RequestStatus, "event_from"))
    to_status: Mapped[RequestStatus] = mapped_column(_enum(RequestStatus, "event_to"))
    changed_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    request: Mapped[DatasetRequest] = relationship(back_populates="events")
    changed_by: Mapped[User] = relationship()


class Assignment(Base):
    """An episode assigned to a request.

    episode_id is the primary key, so the database itself guarantees an episode belongs to at
    most one request, even under concurrent assignment.
    """

    __tablename__ = "assignments"

    episode_id: Mapped[int] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), primary_key=True
    )
    request_id: Mapped[int] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), index=True
    )
    assigned_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    episode: Mapped[Episode] = relationship(back_populates="assignment")
    request: Mapped[DatasetRequest] = relationship(back_populates="assignments")
