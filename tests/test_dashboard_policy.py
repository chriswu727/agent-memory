from contextlib import contextmanager
from types import SimpleNamespace

from membrain.dynamics import DEFAULT_POLICY
from membrain.helpers import Principal
from membrain.service import MemoryService


def test_dashboard_reports_the_effective_default_access_policy(monkeypatch):
    @contextmanager
    def transaction(principal):
        yield SimpleNamespace(execute=lambda *args: [])

    service = MemoryService(SimpleNamespace(transaction=transaction))
    brain = {"id": "brain", "name": "test", "scope_generation": 1, "dependency_generation": 1,
             "durable_sequence": 0, "indexed_sequence": 0}
    monkeypatch.setattr(service, "_brain", lambda *args: brain)
    monkeypatch.setattr(service, "_events", lambda *args, **kwargs: [])
    result = service.dashboard(Principal("tenant", "owner"), "brain")
    assert result["status"]["access_policy"] == DEFAULT_POLICY.version == "contextual-access-v2"
