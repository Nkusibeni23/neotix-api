from datetime import date, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.models import Quality, RequestStatus, Role


def normalise_task(value: str) -> str:
    """'  Pick  Cup ' -> 'pick cup'. Used for requests and imported episodes alike."""
    return " ".join(value.split()).lower()


TaskName = Annotated[
    str, StringConstraints(min_length=1, max_length=255), AfterValidator(normalise_task)
]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- users / auth -------------------------------------------------------------------------


class UserOut(ORM):
    id: int
    email: str
    name: str
    role: Role
    organisation: str | None
    is_active: bool


class UserSummary(ORM):
    id: int
    name: str
    organisation: str | None = None


class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class UserCreate(BaseModel):
    email: EmailStr
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    password: Annotated[str, StringConstraints(min_length=8, max_length=128)]
    role: Role
    organisation: str | None = None


class UserUpdate(BaseModel):
    role: Role | None = None
    is_active: bool | None = None


# --- requests -----------------------------------------------------------------------------


class RequestCreate(BaseModel):
    task_name: TaskName
    episodes_requested: int = Field(gt=0, le=100_000)
    deadline: date
    notes: Annotated[str, StringConstraints(max_length=5000)] | None = None


class StatusEventOut(ORM):
    from_status: RequestStatus | None
    to_status: RequestStatus
    changed_by: UserSummary
    changed_at: datetime


class RequestOut(ORM):
    id: int
    client: UserSummary
    task_name: str
    episodes_requested: int
    episodes_assigned: int
    deadline: date
    notes: str | None
    status: RequestStatus
    created_at: datetime
    updated_at: datetime
    # Statuses the current user may move this request to; the UI renders one button each.
    allowed_transitions: list[RequestStatus] = []


class RequestDetail(RequestOut):
    history: list[StatusEventOut]


class TransitionIn(BaseModel):
    to_status: RequestStatus


class AssignIn(BaseModel):
    episode_ids: list[int] = Field(min_length=1, max_length=1000)


# --- episodes -----------------------------------------------------------------------------


class EpisodeOut(ORM):
    id: int
    episode_id: str
    robot_id: str
    task_name: str
    recorded_at: datetime
    duration_seconds: int
    operator_name: str
    quality: Quality
    request_id: int | None = None


class Page[T](BaseModel):
    items: list[T]
    total: int


class SkippedRow(BaseModel):
    line: int  # line number in the file, header = line 1
    episode_id: str | None
    reason: str


class ImportReport(BaseModel):
    total_rows: int  # non-blank data rows in the file
    imported: int  # new episodes created
    already_present: int  # valid rows whose episode_id was already in the database
    skipped_count: int  # rows rejected (invalid, duplicate or conflicting)
    skipped: list[SkippedRow]  # details, capped for very large files


# --- analytics ----------------------------------------------------------------------------


class EpisodesPerDay(BaseModel):
    day: date
    robot_id: str
    episodes: int


class TaskCount(BaseModel):
    task_name: str
    good_episodes: int


class Fulfilment(BaseModel):
    by_status: dict[RequestStatus, int]
    median_hours_to_delivery: float | None


class AnalyticsOut(BaseModel):
    start: date
    end: date
    episodes_per_day: list[EpisodesPerDay]
    fulfilment: Fulfilment
    top_tasks: list[TaskCount]
