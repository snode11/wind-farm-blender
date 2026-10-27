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
    state_revision: int | None = None
    cleanup_confirmed: bool | None = None
    worker_alive: bool | None = None
    _synchronized_revision: int | None = field(default=None, init=False, repr=False)

    def allows(self, action: str) -> bool:
        if action == "configure":
            return self.confirmed and self.inactive_confirmed and self.run_status in {"READY", "STOPPED"}
        if not self.confirmed:
            return False
        local = self.mode == "demo" and self.connection == "LOCAL DEMO"
        if not local and self.connection != "CONNECTED":
            return False
        if action == "start":
            return self.inactive_confirmed and self.run_status in {"READY", "STOPPED"}
        if action == "step" and self.mode == "formal_training":
            return False
        if action == "reset":
            return self.inactive_confirmed and self.run_status in {"READY", "STOPPED", "FAILED"}
        if action == "pause":
            return self.run_status in {"STARTING", "RUNNING"} and (local or "pause" in self.capabilities)
        if action == "resume":
            return self.run_status == "PAUSED" and (local or "pause" in self.capabilities)
        if action == "step":
            return (self.run_status == "PAUSED" and self.mode != "formal_training"
                    and (local or "single_step" in self.capabilities))
        if action == "stop":
            return (self.run_status in {"STARTING", "RUNNING", "PAUSED"} or
                    self.run_status == 'FAILED' and not self.inactive_confirmed)
        return False

    @property
    def inactive_confirmed(self):
        if self.cleanup_confirmed is not None or self.worker_alive is not None:
            return self.cleanup_confirmed is True and self.worker_alive is False
        return self.run_status in {'READY', 'STOPPED'}

    def _set_cleanup(self, status, cleanup_confirmed, worker_alive):
        if cleanup_confirmed is not None or worker_alive is not None:
            if type(cleanup_confirmed) is not bool or type(worker_alive) is not bool:
                raise ValueError('Cleanup confirmation and worker liveness must both be boolean')
            if cleanup_confirmed and (worker_alive or status not in {'READY', 'STOPPED', 'FAILED'}):
                raise ValueError('Invalid cleanup confirmation')
        self.cleanup_confirmed, self.worker_alive = cleanup_confirmed, worker_alive

    def disconnect(self, error: str = "") -> None:
        self.connection = "DISCONNECTED"
        self.confirmed = False
        self.error = error

    def synchronize(self, session_id: str, status: str, mode: str, capabilities, *, state_revision=None,
                    cleanup_confirmed=None, worker_alive=None) -> None:
        if not session_id or status not in TRANSITIONS or mode not in MODES:
            raise ValueError("Invalid session synchronization")
        if state_revision is not None and (type(state_revision) is not int or state_revision < 0):
            raise ValueError("Invalid state revision")
        self._set_cleanup(status, cleanup_confirmed, worker_alive)
        self.session_id, self.run_status, self.mode = session_id, status, mode
        self.state_revision = self._synchronized_revision = state_revision
        self.capabilities = set(capabilities)
        self.connection, self.confirmed, self.error = "CONNECTED", True, ""

    def lifecycle(self, session_id: str, status: str, mode: str, capabilities, *, state_revision=None,
                  cleanup_confirmed=None, worker_alive=None) -> bool:
        """Retain watermark-covered history without applying it as current state."""
        if not self.confirmed or session_id != self.session_id:
            raise ValueError("Unconfirmed or obsolete session")
        if mode not in MODES or status not in TRANSITIONS:
            raise ValueError("Invalid lifecycle mode")
        capabilities = set(capabilities)
        if any(not isinstance(capability, str) or not capability.strip()
               for capability in capabilities):
            raise ValueError("Invalid lifecycle capabilities")
        if state_revision is not None and (type(state_revision) is not int or state_revision < 0):
            raise ValueError("Invalid state revision")
        if self._synchronized_revision is not None:
            if state_revision is None:
                raise ValueError("Lifecycle state revision required after versioned synchronization")
            if state_revision <= self._synchronized_revision:
                return False
            if state_revision <= self.state_revision:
                raise ValueError("Lifecycle state revision must increase")
        if status != self.run_status and status not in TRANSITIONS.get(self.run_status, set()):
            raise ValueError(f"Invalid lifecycle transition: {self.run_status} -> {status}")
        self._set_cleanup(status, cleanup_confirmed, worker_alive)
        self.run_status, self.mode, self.capabilities = status, mode, capabilities
        self.state_revision = state_revision
        return True
