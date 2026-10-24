import copy
import importlib
import json
from pathlib import Path

import pytest
from test_conversation_evaluation import context_stage, update_context
from test_semantic import reference


@pytest.fixture
def evaluation(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    return importlib.import_module("examples.evaluate_conversation")


def request_stage(evaluation):
    stage = context_stage(evaluation)
    source = stage["sources"]["input_events"][0]
    refs = [reference(source)]
    item = {"proposal_id": "current-request", "kind": "goal", "key": "recall_arrangement",
            "value": "用户询问复习安排与是否约好饮料。", "evidence_use": "current_request", "evidence_refs": refs,
            "request_verification": {"state": "supported_request", "method": "semantic_model",
                                     "not_a_truth_certificate": True}}
    stage["runtime"].update(turn_id="turn-current", orientation_job_id="orient-current",
                             input_event_refs=[{"event_id": source["event_id"], "revision": source["revision"]}])
    stage["orientation"]["result"].update(
        current_input_refs=copy.deepcopy(stage["runtime"]["input_event_refs"]),
        current_input_binding={"turn_id": "turn-current", "job_id": "orient-current"},
        situation_updates=[item], verification_assessments=[{
            "proposal_id": item["proposal_id"], "request_verdict": "supports", "proposal_verdict": "supports",
            "link_verdict": None, "relations": [{"evidence_index": 0, "verdict": "supports"}]}],
    )
    view = json.loads(stage["model_input"]["memory_context"])
    view["sources"]["s:request"] = {key: value for key, value in refs[0].items() if key != "source_content_sha256"}
    view["blocks"].append({"section": "current_situation", "content": {
        **{key: item[key] for key in ("kind", "key", "value", "evidence_use")},
        "use": "current_request_not_historical_evidence"}, "sources": ["s:request"]})
    update_context(evaluation, stage, view)
    return stage


def test_current_request_audit_follows_bound_verified_request_into_actual_host_context(evaluation):
    stage = request_stage(evaluation)
    audit = evaluation.current_request_audit(stage)
    assert all(audit["checks"].values())
    assert audit["rendered_requests"][0]["sources"] == stage["orientation"]["result"]["situation_updates"][0]["evidence_refs"]
    assert all(evaluation.context_audit(stage)["checks"].values())


@pytest.mark.parametrize("damage", [
    "wrong_turn", "wrong_input", "no_request", "missing_mark", "missing_verdict", "whole_unknown",
    "old_source", "bad_hash", "unrendered", "changed_meaning", "history_label", "missing_rendered_source",
])
def test_noop_or_mislabeled_orientation_is_not_a_successful_request_test(evaluation, damage):
    stage = request_stage(evaluation)
    result = stage["orientation"]["result"]
    view = json.loads(stage["model_input"]["memory_context"])
    item = result["situation_updates"][0]
    if damage == "wrong_turn":
        result["current_input_binding"]["turn_id"] = "old-turn"
    elif damage == "wrong_input":
        result["current_input_refs"] = []
    elif damage == "no_request":
        result["situation_updates"] = []
    elif damage == "missing_mark":
        item.pop("request_verification")
    elif damage == "missing_verdict":
        result["verification_assessments"][0].pop("request_verdict")
    elif damage == "whole_unknown":
        result["verification_assessments"][0]["proposal_verdict"] = "unknown"
    elif damage == "old_source":
        item["evidence_refs"] = [reference(stage["before"]["sources"][0])]
    elif damage == "bad_hash":
        item["evidence_refs"][0]["source_content_sha256"] = "0" * 64
    elif damage == "unrendered":
        view["blocks"].pop()
    elif damage == "changed_meaning":
        view["blocks"][-1]["content"]["value"] = "双方确实约好喝茶。"
    elif damage == "history_label":
        view["blocks"][-1]["content"]["use"] = "source_attributed_applicable_memory"
    else:
        view["blocks"][-1]["sources"] = []
    update_context(evaluation, stage, view)
    assert not all(evaluation.current_request_audit(stage)["checks"].values())
