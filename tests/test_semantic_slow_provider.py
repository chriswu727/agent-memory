import pytest
from test_semantic import batch, claim, encoded, event, proposals, verifier

from agent_memory import semantic
from agent_memory.semantic import SemanticPolicy, SemanticProcessor


@pytest.mark.parametrize("limit,expected", [(240000, "provider_unavailable"), (600000, "completed")])
def test_explicit_time_allowance_covers_both_calls_without_resetting_deadline(monkeypatch, limit, expected):
    elapsed = [0.0]
    calls, settlements = [], []
    monkeypatch.setattr(semantic.time, "monotonic", lambda: elapsed[0])
    source = event("工作时请直接给我建议。")

    def invoke(request, duration, response):
        calls.append((request.phase, request.timeout_seconds))
        elapsed[0] += duration
        if duration > request.timeout_seconds:
            raise TimeoutError
        return response

    def propose(request):
        return invoke(request, 172, encoded(proposals(claim(source))))

    def verify(request):
        return invoke(request, 100, verifier(request))

    result = SemanticProcessor(propose, verify, SemanticPolicy(max_wall_time_ms=limit)).process(
        batch(source), reserve=lambda **_: None, on_call_result=lambda **value: settlements.append(value),
    )
    assert result.status == expected
    assert calls == [("proposal", limit / 1000), ("verification", limit / 1000 - 172)]
    assert result.model_calls == 2
    assert len(result.claims) == (1 if expected == "completed" else 0)
    assert [item["outcome"] for item in settlements] == [
        "completed", "completed" if expected == "completed" else "uncertain",
    ]
