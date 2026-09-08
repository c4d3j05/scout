"""Second sample source file for the scout benchmark demo (fixtures/).

Stands in for a LangGraph-style node registry — the kind of file a scout
delegation would be asked to summarise ("list every function that mutates agent
state, with line numbers"). Kept intentionally small.
"""


class NodeRegistry:
    def __init__(self):
        self._nodes = {}
        self._state = {}

    def register(self, name, fn):
        """Add a node. Mutates registry state."""
        self._nodes[name] = fn
        self._state[name] = "registered"
        return self

    def resolve(self, name):
        return self._nodes.get(name)

    def mark_running(self, name):
        """Mutates agent state for a node."""
        self._state[name] = "running"

    def mark_done(self, name):
        """Mutates agent state for a node."""
        self._state[name] = "done"

    def snapshot(self):
        return dict(self._state)


def build_default_registry():
    reg = NodeRegistry()
    reg.register("ingest", lambda x: x)
    reg.register("plan", lambda x: x)
    reg.register("act", lambda x: x)
    return reg
