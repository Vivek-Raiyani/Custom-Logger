"""
Shared context state for the Lycan SDK.

The ContextVar here is the bridge between the middleware (which knows
which service a request belongs to) and the handler (which runs in
emit() and needs that information per log record).

Because FastAPI is async and uses asyncio tasks per request, ContextVar
values are automatically scoped to each request — no risk of one
request's service label leaking into another.
"""

from contextvars import ContextVar

# Set by LycanMiddleware at the start of each request.
# Read by RemoteLogHandler.emit() — falls back to record.name if not set
# (i.e. when middleware is not in use / background tasks / startup logs).
_current_service_label: ContextVar[str | None] = ContextVar(
    "lycan_service_label", default=None
)


def get_service_label() -> str | None:
    return _current_service_label.get()


def set_service_label(value: str) -> object:
    """Returns the token needed to reset the var later."""
    return _current_service_label.set(value)


def reset_service_label(token: object) -> None:
    _current_service_label.reset(token)