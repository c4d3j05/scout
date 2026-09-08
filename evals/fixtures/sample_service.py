"""Sample source file for the scout benchmark demo (fixtures/).

This is a small, self-contained module used only to illustrate the benchmark's
chars/4 worker-token estimate and to stand in for a "large module" scout would be
asked to orient in. It is NOT wired into run.sh (run.sh generates its own fixtures
at exact line counts in a temp dir); this file exists so the demo has a couple of
realistic source files to point at.
"""

from dataclasses import dataclass


@dataclass
class Request:
    trace_id: str
    payload: dict


class GravityClient:
    """Stand-in for the real client used across services."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self._session = None

    def connect(self):
        self._session = object()
        return self._session

    def send(self, req: Request) -> dict:
        if self._session is None:
            self.connect()
        return {"ok": True, "trace_id": req.trace_id, "echo": req.payload}


def orchestrate(req: Request, client: GravityClient) -> dict:
    """Route a request through the (fake) agent layer and mutate its state."""
    state = {"step": 0, "trace_id": req.trace_id}
    state["step"] += 1
    resp = client.send(req)
    state["step"] += 1
    state["result"] = resp
    return state
