from dataclasses import dataclass
from typing import Literal

states = {
    0: "OK",
    1: "WARNING",
    2: "CRITICAL",
    3: "UNKNOWN",
}


@dataclass
class NagiosResult:
    status: Literal["ok", "warning", "critical", "unknown"]
    message: str