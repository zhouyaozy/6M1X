from dataclasses import dataclass
from typing import Literal

from scripts.lib.zulip_tools import atomic_nagios_write

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

    def write(self, check_name: str) -> None:
        atomic_nagios_write(check_name, self.status, self.message)
