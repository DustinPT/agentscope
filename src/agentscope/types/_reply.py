# -*- coding: utf-8 -*-
"""Reply termination vocabulary shared across events and messages."""

from enum import StrEnum


class ReplyFinishedReason(StrEnum):
    """The reason a reply finished."""

    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    EXCEED_MAX_ITERS = "exceed_max_iters"
    ERROR = "error"
