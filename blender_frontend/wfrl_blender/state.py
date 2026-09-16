"""Frontend connection and backend lifecycle state."""
from dataclasses import dataclass, field
from enum import StrEnum

class RunStatus(StrEnum):
    READY = "READY"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    DRAINING = "DRAINING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


MODES = {"demo", "interactive_training", "formal_training", "replay"}
ACTIVE_STATUSES = {"STARTING", "RUNNING", "PAUSED", "DRAINING"}
TRANSITIONS = {
    "READY": {"STARTING"}, "STARTING": {"RUNNING", "PAUSED", "DRAINING", "FAILED"},
    "RUNNING": {"PAUSED", "DRAINING", "FAILED"},
    "PAUSED": {"STARTING", "RUNNING", "DRAINING", "FAILED"},
    "DRAINING": {"STOPPED", "FAILED"}, "STOPPED": {"READY", "STARTING"},
    "FAILED": {"READY"},
}


@dataclass
class FrontendState:
    """Connection and authoritative lifecycle are independent of Demo's timeline."""
    mode: str = "demo"
    connection: str = "LOCAL DEMO"
    run_status: str = "READY"
    confirmed: bool = True
    session_id: str = ""
    run_id: str | None = None
    capabilities: set[str] = field(default_factory=set)
    error: str = ""

    def allows(self, action: str) -> bool:
        if action == "configure":
            return self.confirmed and self.run_status in {"READY", "STOPPED"}
        if not self.confirmed:
            return False
        local = self.mode == "demo" and self.connection == "LOCAL DEMO"
        if not local and self.connection != "CONNECTED":
            return False
        if action == "start":
            return self.run_status in {"READY", "STOPPED"}
        if action == "step" and self.mode == "formal_training":
            return False
        if action == "reset":
            return self.run_status in {"READY", "STOPPED", "FAILED"}
        if action == "pause":
            return self.run_status in {"STARTING", "RUNNING"} and (local or "pause" in self.capabilities)
        if action == "resume":
            return self.run_status == "PAUSED" and (local or "pause" in self.capabilities)
        if action == "step":
            return (self.run_status == "PAUSED" and self.mode != "formal_training"
                    and (local or "single_step" in self.capabilities))
        if action == "stop":
            return self.run_status in {"STARTING", "RUNNING", "PAUSED"}
        return False

    def disconnect(self, error: str = "") -> None:
        self.connection = "DISCONNECTED"
        self.confirmed = False
        self.error = error

    def synchronize(self, session_id: str, status: str, mode: str, capabilities) -> None:
        if not session_id or status not in TRANSITIONS or mode not in MODES:
            raise ValueError("Invalid session synchronization")
        self.session_id, self.run_status, self.mode = session_id, status, mode
        self.capabilities = set(capabilities)
        self.connection, self.confirmed, self.error = "CONNECTED", True, ""

    def lifecycle(self, session_id: str, status: str, mode: str, capabilities) -> None:
        if not self.confirmed or session_id != self.session_id:
            raise ValueError("Unconfirmed or obsolete session")
        if mode not in MODES:
            raise ValueError("Invalid lifecycle mode")
        capabilities = set(capabilities)
        if any(not isinstance(capability, str) or not capability.strip()
               for capability in capabilities):
            raise ValueError("Invalid lifecycle capabilities")
        if status != self.run_status and status not in TRANSITIONS.get(self.run_status, set()):
            raise ValueError(f"Invalid lifecycle transition: {self.run_status} -> {status}")
        self.run_status, self.mode, self.capabilities = status, mode, capabilities
