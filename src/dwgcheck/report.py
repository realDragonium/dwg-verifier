from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

Tier = Literal[1, 2]


class Status(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


@dataclass(frozen=True)
class Finding:
    tier: Tier
    check: str
    message: str
    offset: int | None = None
    section: str | None = None

    def location(self) -> str:
        parts = []
        if self.section is not None:
            parts.append(self.section)
        if self.offset is not None:
            parts.append(f"0x{self.offset:x}")
        return " @ ".join(parts) if parts else "-"


@dataclass
class FileReport:
    file: str
    status: Status
    version: str | None = None
    findings: list[Finding] = field(default_factory=list)
    tiers_run: list[Tier] = field(default_factory=list)
    note: str | None = None
