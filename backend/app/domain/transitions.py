"""Download task state transitions: the legal lifecycle graph.

The PRD (v1) defines five download states: ``pending``, ``downloading``,
``completed``, ``failed``, ``expired``. This module is the single authority on
which moves are legal; the ORM only stores the value (Task 4), the worker and
application services (Tasks 7-12) must route every status change through
:func:`transition`.

The designed graph (all edges are one-way):

* ``pending -> downloading``  — the worker claims a queued task.
* ``pending -> failed``       — the task is rejected before download starts
  (e.g. the source vanished or the request was invalid).
* ``pending -> expired``      — never claimed before the bubble cleanup pass.
* ``downloading -> completed`` — download finished and was verified.
* ``downloading -> failed``   — download error, or cancelled by the user.
* ``downloading -> expired``  — interrupted and swept by bubble cleanup.
* ``completed -> expired``    — the 24 h bubble retention expired.
* ``failed -> pending``       — user retry; Task 11 caps retries at 3, so the
  *caller* (not this graph) enforces the retry budget.
* ``expired -> pending``      — re-download after expiry (PRD: 重新下载).

Decisions, documented:

* **Same-state moves are illegal.** No state may transition to itself; callers
  that receive a no-op update (e.g. a progress tick while ``downloading``)
  should simply not call :func:`transition` rather than rely on idempotency.
* **``failed``/``expired`` are not terminal.** The PRD offers "重新下载"
  (re-download) from both, modeled here as a re-claim back to ``pending``.
* **Retry budgeting lives outside the graph.** ``failed -> pending`` is always
  legal; the retry cap (3, Task 11) is a worker policy layered on top.

The graph is immutable by convention (module-level mapping); tests assert its
exact shape so any deliberate change is a reviewed change.
"""

from __future__ import annotations

from app.domain.enums import DownloadStatus

__all__ = ["ALLOWED_TRANSITIONS", "IllegalTransitionError", "transition"]

ALLOWED_TRANSITIONS: dict[DownloadStatus, set[DownloadStatus]] = {
    DownloadStatus.PENDING: {
        DownloadStatus.DOWNLOADING,
        DownloadStatus.FAILED,
        DownloadStatus.EXPIRED,
    },
    DownloadStatus.DOWNLOADING: {
        DownloadStatus.COMPLETED,
        DownloadStatus.FAILED,
        DownloadStatus.EXPIRED,
    },
    DownloadStatus.COMPLETED: {DownloadStatus.EXPIRED},
    DownloadStatus.FAILED: {DownloadStatus.PENDING},
    DownloadStatus.EXPIRED: {DownloadStatus.PENDING},
}


class IllegalTransitionError(ValueError):
    """Raised when a download task attempts a state change the graph forbids."""


def transition(
    current: DownloadStatus | str, next_: DownloadStatus | str
) -> DownloadStatus:
    """Validate and perform a download status transition.

    Accepts enum members or their string values (``"pending"``); returns the
    ``next_`` enum member on success and raises
    :class:`IllegalTransitionError` when the move is not in
    :data:`ALLOWED_TRANSITIONS`. Unknown strings raise
    :class:`ValueError` (via ``DownloadStatus`` lookup); other types raise
    :class:`TypeError`.
    """
    current = _coerce_status(current)
    next_ = _coerce_status(next_)
    if next_ not in ALLOWED_TRANSITIONS[current]:
        raise IllegalTransitionError(
            f"illegal download status transition: "
            f"{current.value!r} -> {next_.value!r}"
        )
    return next_


def _coerce_status(value: DownloadStatus | str) -> DownloadStatus:
    if isinstance(value, DownloadStatus):
        return value
    if isinstance(value, str):
        return DownloadStatus(value)
    raise TypeError(
        f"expected DownloadStatus or str, got {type(value).__name__}"
    )
