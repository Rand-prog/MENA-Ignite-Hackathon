"""Process-wide demo state that isn't a trip/traveller row: the agent kill
switch. Single backend process, per the demo contract — module-level state
is enough and matches FakeBackend's own approach in run_demo.py."""
from __future__ import annotations


class RuntimeState:
    def __init__(self) -> None:
        self.agent_model_enabled: bool = True

    def reset(self) -> None:
        self.agent_model_enabled = True


runtime_state = RuntimeState()
