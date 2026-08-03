from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class RunStatus:
    run_id: str
    stage: str
    status: StageStatus
    message: str = ""
