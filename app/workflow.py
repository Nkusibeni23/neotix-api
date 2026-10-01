"""Request status machine. Pure functions, no database, so the rules are easy to test and read.

submitted -> in_progress -> delivered -> accepted
                                      -> rejected -> in_progress (rework)
"""

from app.models import RequestStatus as S
from app.models import Role

STAFF = frozenset({Role.operator, Role.admin})
CLIENT = frozenset({Role.client})

# (from, to) -> roles allowed to make that move. Anything not listed is invalid.
TRANSITIONS: dict[tuple[S, S], frozenset[Role]] = {
    (S.submitted, S.in_progress): STAFF,
    (S.in_progress, S.delivered): STAFF,
    (S.delivered, S.accepted): CLIENT,
    (S.delivered, S.rejected): CLIENT,
    (S.rejected, S.in_progress): STAFF,
}

# Episodes can only be added or removed while an operator is working on the request.
ASSIGNABLE_STATUSES = frozenset({S.in_progress})


class TransitionError(Exception):
    """Raised with a message that is safe to show to the user."""

    def __init__(self, message: str, *, forbidden: bool = False):
        super().__init__(message)
        self.forbidden = forbidden  # True -> 403 (wrong role), False -> 409 (wrong state)


def allowed_next(current: S, role: Role) -> list[S]:
    """Statuses this role may move a request to from `current` (drives the UI buttons)."""
    return [to for (frm, to), roles in TRANSITIONS.items() if frm == current and role in roles]


def check_transition(
    current: S, target: S, role: Role, *, episodes_assigned: int, episodes_requested: int
) -> None:
    roles = TRANSITIONS.get((current, target))
    if roles is None:
        raise TransitionError(f"Cannot move a request from {current} to {target}")
    if role not in roles:
        raise TransitionError(f"Your role cannot move a request to {target}", forbidden=True)
    if target == S.delivered and episodes_assigned < episodes_requested:
        raise TransitionError(
            f"Request needs {episodes_requested} episodes before it can be delivered "
            f"({episodes_assigned} assigned)"
        )
