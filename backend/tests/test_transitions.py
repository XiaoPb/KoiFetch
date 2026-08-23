"""Tests for the download state transition rules (``app/domain/transitions.py``).

The legal graph is exercised both directionally (every legal edge succeeds,
every illegal edge raises :class:`IllegalTransitionError`) and exhaustively
over all 5 x 5 state pairs, so a future edit to ``ALLOWED_TRANSITIONS`` cannot
silently change behavior.
"""

import pytest

from app.domain.enums import DownloadStatus
from app.domain.transitions import (
    ALLOWED_TRANSITIONS,
    IllegalTransitionError,
    transition,
)

ALL_STATUSES = list(DownloadStatus)


class TestAllowedTransitions:
    def test_covers_every_status(self):
        assert set(ALLOWED_TRANSITIONS) == set(ALL_STATUSES)

    def test_no_self_transition(self):
        for status in ALL_STATUSES:
            assert status not in ALLOWED_TRANSITIONS[status]

    def test_every_legal_edge_returns_next(self):
        for current, allowed in ALLOWED_TRANSITIONS.items():
            for next_ in allowed:
                assert transition(current, next_) is next_

    def test_every_illegal_edge_raises(self):
        for current in ALL_STATUSES:
            for next_ in ALL_STATUSES:
                if next_ in ALLOWED_TRANSITIONS[current]:
                    continue
                with pytest.raises(IllegalTransitionError):
                    transition(current, next_)

    def test_legal_graph_shape(self):
        # The full legal graph, as designed (documented in transitions.py):
        assert ALLOWED_TRANSITIONS[DownloadStatus.PENDING] == {
            DownloadStatus.DOWNLOADING,
            DownloadStatus.FAILED,
            DownloadStatus.EXPIRED,
        }
        assert ALLOWED_TRANSITIONS[DownloadStatus.DOWNLOADING] == {
            DownloadStatus.COMPLETED,
            DownloadStatus.FAILED,
            DownloadStatus.EXPIRED,
        }
        assert ALLOWED_TRANSITIONS[DownloadStatus.COMPLETED] == {
            DownloadStatus.EXPIRED
        }
        assert ALLOWED_TRANSITIONS[DownloadStatus.FAILED] == {
            DownloadStatus.PENDING
        }
        assert ALLOWED_TRANSITIONS[DownloadStatus.EXPIRED] == {
            DownloadStatus.PENDING
        }


class TestTransitionDetails:
    def test_accepts_string_statuses(self):
        assert transition("pending", "downloading") is DownloadStatus.DOWNLOADING

    def test_accepts_mixed_enum_and_string(self):
        assert transition(DownloadStatus.FAILED, "pending") is DownloadStatus.PENDING

    def test_invalid_string_status_rejected(self):
        with pytest.raises(ValueError):
            transition("pending", "finished")

    def test_non_status_type_rejected(self):
        with pytest.raises(TypeError):
            transition(DownloadStatus.PENDING, 42)

    def test_error_message_names_both_states(self):
        with pytest.raises(IllegalTransitionError, match="completed.*pending"):
            transition(DownloadStatus.COMPLETED, DownloadStatus.PENDING)

    def test_same_to_same_is_illegal(self):
        for status in ALL_STATUSES:
            with pytest.raises(IllegalTransitionError):
                transition(status, status)
