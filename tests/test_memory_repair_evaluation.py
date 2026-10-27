import copy
import importlib
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from test_belief_semantic import abstraction

from membrain.semantic import SemanticResult


@pytest.fixture
def evaluation(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    return importlib.import_module("examples.evaluate_memory_repairs")


def diagnostic():
    data, proposed = abstraction()
    data = replace(data, consolidation_plan={"objectives": [{
        "instance_memory_versions": proposed["belief_links"][0]["target_memory_versions"]}]})
    result = SemanticResult("completed", claims=tuple(copy.deepcopy(proposed["claims"])),
                            belief_links=tuple(copy.deepcopy(proposed["belief_links"])), model_calls=2)
    responses = {"proposal": proposed, "verification": {"assessments": [
        {"proposal_id": proposed["claims"][0]["proposal_id"], "proposal_verdict": "supports",
         "link_verdict": None, "supporting_evidence_indices": [0, 1],
         "relations": [{"evidence_index": index, "verdict": "supports"} for index in range(2)]},
        {"proposal_id": proposed["belief_links"][0]["proposal_id"], "proposal_verdict": "supports",
         "link_verdict": "supports", "supporting_evidence_indices": [0, 1],
         "relations": [{"evidence_index": index, "verdict": "supports"} for index in range(2)],
         "instance_verdicts": [
             {"memory_version": parent, "verdict": "supports"}
             for parent in proposed["belief_links"][0]["target_memory_versions"]]},
    ]}}
    maps = {proposed["belief_links"][0]["proposal_id"]: [
        {"memory_version": parent, "evidence_indices": [index]}
        for index, parent in enumerate(proposed["belief_links"][0]["target_memory_versions"])]}
    return data, result, responses, maps


def test_successful_structure_still_requires_review_of_actual_meaning(evaluation):
    data, result, responses, maps = diagnostic()
    review = evaluation.abstraction_review(data, result, responses, maps)
    assert all(review["checks"].values())
    assert review["semantic_quality"] == "requires_human_review_of_whole_claim_and_evidence"
    assert review["accepted_abstractions"][0]["claim"]["statement"] == result.claims[0]["statement"]
    assert len(review["source_texts"]) == 2


@pytest.mark.parametrize("fault", ["parent", "endpoint", "evidence", "condition", "world", "verdict", "no_link"])
def test_matching_words_cannot_hide_structural_abstraction_failure(evaluation, fault):
    data, result, responses, maps = diagnostic()
    if fault == "parent":
        result.claims[0]["source_memory_versions"].pop()
    elif fault == "endpoint":
        result.belief_links[0]["target_memory_versions"].pop()
    elif fault == "evidence":
        result.belief_links[0]["evidence_refs"].pop()
    elif fault == "condition":
        result.claims[0]["conditions"] = {"op": "unresolved", "label": "state"}
    elif fault == "world":
        result.claims[0]["world"] = "dream"
    elif fault == "verdict":
        responses["verification"]["assessments"][0]["proposal_verdict"] = "unknown"
    else:
        result = replace(result, belief_links=())
    assert not evaluation.abstraction_review(data, result, responses, maps)["checks"]["verified_abstraction_accepted"]


def test_rejected_whole_claim_is_preserved_for_review_and_not_a_success(evaluation):
    data, result, responses, maps = diagnostic()
    rejected = ({"proposal_id": result.claims[0]["proposal_id"], "reason": "semantic_support_unconfirmed"},)
    result = replace(result, claims=(), belief_links=(), rejected=rejected)
    review = evaluation.abstraction_review(data, result, responses, maps)
    assert review["rejected_schemas"] == responses["proposal"]["claims"]
    assert review["checks"]["rejected_schemas_not_accepted"]
    assert not review["checks"]["verified_abstraction_accepted"]


def test_literal_source_cannot_substitute_for_the_original_english_assertion(evaluation):
    target = {"memory_id": "semantic", "revision": 1, "statement": "Simultaneous reports conflict."}
    literal = {"memory_id": "literal", "revision": 1, "statement": "备考记录互相冲突。"}
    observation = {"items": [{"memory": literal}], "blocks": [{"section": "relevant_memory",
                   "text": json.dumps({"memory_version": {"memory_id": "literal", "revision": 1}})}],
                   "context_usage": {"recall": 10, "prepare": 10}, "context_budget": 16000}
    checks = evaluation.replay_checks(target, observation)
    assert checks["english_assertion_has_no_direct_chinese_lexical_match"]
    assert not checks["exact_english_assertion_recalled"]
    assert not checks["exact_english_assertion_prepared"]
    observation["items"] = [{"memory": copy.deepcopy(target), "rank_signals": [{
        "method": "keyword", "model_or_index_version": evaluation.SOURCE_ROUTE_VERSION}]}]
    observation["blocks"][0]["text"] = json.dumps({"memory_version": {"memory_id": "semantic", "revision": 1}})
    assert all(evaluation.replay_checks(target, observation).values())


def test_saved_snapshot_changes_fail_before_a_replay(evaluation):
    old = {"memory_id": "a", "revision": 1, "statement": "old"}
    with pytest.raises(ValueError, match="snapshot has changed"):
        evaluation.confirm_documents([old], [{**old, "statement": "new"}])


def test_frozen_input_hash_is_checked_before_provider_construction(evaluation):
    with pytest.raises(ValueError, match="modified"):
        evaluation.batch_from_saved({"synthetic_only": True, "batch_sha256": "old",
                                     "batch": {"batch_id": "batch", "sources": []}})


def test_no_mode_never_loads_settings_or_calls_a_provider(evaluation, monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("No mode cannot load environment or call the provider")
    monkeypatch.setattr(evaluation.Settings, "from_env", forbidden)
    monkeypatch.setattr(evaluation, "ChatCompletionsSemanticProvider", forbidden)
    monkeypatch.setattr(sys, "argv", ["evaluate_memory_repairs", "--output", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        evaluation.main()
    assert exc.value.code == 2 and not list(tmp_path.iterdir())


@pytest.mark.parametrize("state", ["started", "interrupted"])
def test_interrupted_diagnostic_is_never_automatically_repeated(evaluation, tmp_path, state):
    path = tmp_path / "live.json"
    path.write_text(json.dumps({"state": state}))
    with pytest.raises(ValueError, match="will not be repeated"):
        evaluation.resume_report(path)


def test_finished_resume_never_loads_dotenv_or_provider(evaluation, monkeypatch, tmp_path):
    report = {"state": "finished", "review": {"checks": {"verified_abstraction_accepted": True}},
              "usage_after": {"reserved_calls": 2}}
    (tmp_path / "live.json").write_text(json.dumps(report))
    def forbidden(*args, **kwargs):
        pytest.fail("Resume must only read the saved report")
    monkeypatch.setattr(evaluation, "load_dotenv", forbidden)
    monkeypatch.setattr(evaluation, "ChatCompletionsSemanticProvider", forbidden)
    monkeypatch.setattr(sys, "argv", ["evaluate_memory_repairs", "--resume", "--output", str(tmp_path)])
    evaluation.main()


@pytest.mark.parametrize("fault", ["missing_verdict", "duplicate_verdict", "unknown_verdict",
                                  "wrong_revision", "missing_map", "empty_indices", "invalid_index"])
def test_abstraction_requires_complete_supported_instance_assessments(evaluation, fault):
    data, result, responses, maps = diagnostic()
    verdicts = responses["verification"]["assessments"][1]["instance_verdicts"]
    support = next(iter(maps.values()))
    if fault == "missing_verdict":
        verdicts.pop()
    elif fault == "duplicate_verdict":
        verdicts.append(copy.deepcopy(verdicts[0]))
    elif fault == "unknown_verdict":
        verdicts[0]["verdict"] = "unknown"
    elif fault == "wrong_revision":
        verdicts[0]["memory_version"] = {**verdicts[0]["memory_version"], "revision": 999}
    elif fault == "missing_map":
        maps.clear()
    elif fault == "empty_indices":
        support[0]["evidence_indices"] = []
    else:
        support[0]["evidence_indices"] = [999]
    assert not evaluation.abstraction_review(data, result, responses, maps)["checks"]["verified_abstraction_accepted"]


@pytest.mark.parametrize("fault", ["missing", "duplicate", "unknown", "irrelevant_in_basis",
                                  "changed_statement", "unfiltered_irrelevant", "partial_basis"])
def test_supported_basis_cannot_hide_unknowns_or_rewrite_claim(evaluation, fault):
    data, result, responses, maps = diagnostic()
    assessment = responses["verification"]["assessments"][0]
    if fault == "missing":
        assessment.pop("supporting_evidence_indices")
    elif fault == "duplicate":
        assessment["supporting_evidence_indices"].append(0)
    elif fault == "unknown":
        assessment["relations"][1]["verdict"] = "unknown"
        assessment["supporting_evidence_indices"] = [0]
        result.claims[0]["evidence_refs"].pop()
    elif fault == "irrelevant_in_basis":
        assessment["relations"][1]["verdict"] = "irrelevant"
    elif fault == "changed_statement":
        result.claims[0]["statement"] += " This always happens."
    elif fault == "unfiltered_irrelevant":
        assessment["relations"][1]["verdict"] = "irrelevant"
        assessment["supporting_evidence_indices"] = [0]
    else:
        assessment["supporting_evidence_indices"] = [0]
    assert not evaluation.abstraction_review(data, result, responses, maps)["checks"]["verified_abstraction_accepted"]


def test_explicit_irrelevant_citation_can_be_removed_without_changing_claim(evaluation):
    data, result, responses, maps = diagnostic()
    extra = copy.deepcopy(responses["proposal"]["claims"][0]["evidence_refs"][0])
    extra["span"]["start"] = 1
    responses["proposal"]["claims"][0]["evidence_refs"].append(extra)
    result.belief_links[0]["evidence_refs"].append(copy.deepcopy(extra))
    for assessment in responses["verification"]["assessments"]:
        assessment["relations"].append({"evidence_index": 2, "verdict": "irrelevant"})
    review = evaluation.abstraction_review(data, result, responses, maps)
    assert review["checks"]["verified_abstraction_accepted"]
    assert len(review["accepted_abstractions"][0]["claim"]["evidence_refs"]) == 2
    assert len(review["accepted_abstractions"][0]["link"]["evidence_refs"]) == 3


def followup_fixture(evaluation, tmp_path, calls=2):
    from types import SimpleNamespace

    ledger = tmp_path / "calls.jsonl"
    records = []
    for index in range(calls):
        records.extend([
            {"event": "reserved", "request_id": str(index), "reserved_output_tokens": 16384},
            {"event": "completed", "request_id": str(index), "usage_confirmed": True,
             "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300}},
        ])
    ledger.write_text("".join(json.dumps(item) + "\n" for item in records))
    meter = evaluation.MeteredTransport(ledger, max_calls=4, max_output_tokens=65536)
    original = {"state": "finished", "synthetic_only": True, "batch_id": "batch",
                "review": {"checks": {"verified_abstraction_accepted": False}},
                "result": {"model_calls": 2}, "usage_before": {"reserved_calls": 0},
                "usage_after": meter.summary()}
    (tmp_path / "live.json").write_text(json.dumps(original))
    return SimpleNamespace(batch_id="batch"), meter, original


def test_one_diagnosed_followup_preserves_original_and_shared_ledger(evaluation, monkeypatch, tmp_path):
    batch, meter, original = followup_fixture(evaluation, tmp_path)
    meter.close()
    old_report = (tmp_path / "live.json").read_bytes()
    old_ledger = (tmp_path / "calls.jsonl").read_bytes()
    limits = []
    transport = evaluation.MeteredTransport

    def tracked(*args, **kwargs):
        limits.append(kwargs)
        return transport(*args, **kwargs)

    def unavailable(*args, **kwargs):
        raise RuntimeError("private provider detail")

    monkeypatch.setattr(evaluation, "MeteredTransport", tracked)
    monkeypatch.setattr(evaluation, "ChatCompletionsSemanticProvider", unavailable)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-key")
    report = evaluation.run_live(batch, tmp_path / "followup.json", tmp_path / "calls.jsonl",
                                 followup_reason="Explicit irrelevant evidence contract repair")
    assert limits == [{"max_calls": 4, "max_output_tokens": 65536, "capture_content": False}]
    assert report["followup"]["prior_report_sha256"] == evaluation.digest(original)
    assert report["usage_before"]["reserved_calls"] == 2
    assert report["policy"]["max_model_calls"] == 2
    assert report["state"] == "interrupted"
    assert "private provider detail" not in json.dumps(report)
    assert (tmp_path / "live.json").read_bytes() == old_report
    assert (tmp_path / "calls.jsonl").read_bytes() == old_ledger
    with pytest.raises(ValueError, match="already recorded"):
        evaluation.run_live(batch, tmp_path / "followup.json", tmp_path / "calls.jsonl",
                            followup_reason="Trying again")


@pytest.mark.parametrize("fault", ["success", "interrupted", "unfinished_call", "changed_usage",
                                  "wrong_batch", "only_one_call", "third_call", "wrong_path"])
def test_followup_requires_exact_failed_original_and_two_finished_calls(evaluation, tmp_path, fault):
    batch, meter, original = followup_fixture(evaluation, tmp_path, calls=3 if fault == "third_call" else 2)
    path = tmp_path / "followup.json"
    if fault == "success":
        original["review"]["checks"]["verified_abstraction_accepted"] = True
    elif fault == "interrupted":
        original["state"] = "interrupted"
    elif fault == "unfinished_call":
        original["usage_after"]["completed_calls"] = 1
    elif fault == "changed_usage":
        original["usage_after"]["usage_totals"]["total_tokens"] += 1
    elif fault == "wrong_batch":
        original["batch_id"] = "different"
    elif fault == "only_one_call":
        original["result"]["model_calls"] = 1
    elif fault == "wrong_path":
        path = tmp_path / "live.json"
    (tmp_path / "live.json").write_text(json.dumps(original))
    try:
        with pytest.raises(ValueError):
            evaluation.followup_metadata(batch, path, meter, "diagnosed")
    finally:
        meter.close()


def test_default_diagnostic_cannot_reuse_previously_charged_ledger(evaluation, tmp_path):
    batch, meter, _ = followup_fixture(evaluation, tmp_path)
    meter.close()
    with pytest.raises(ValueError, match="ledger already contains"):
        evaluation.run_live(batch, tmp_path / "other.json", tmp_path / "calls.jsonl")


def test_followup_flag_cannot_make_free_replay_billable(evaluation, monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid mode cannot load settings or access provider")
    monkeypatch.setattr(evaluation.Settings, "from_env", forbidden)
    monkeypatch.setattr(evaluation, "ChatCompletionsSemanticProvider", forbidden)
    monkeypatch.setattr(sys, "argv", ["evaluate_memory_repairs", "--replay", "--followup", "reason",
                                     "--output", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        evaluation.main()
    assert exc.value.code == 2


def test_explicit_followup_resume_only_reads_followup_report(evaluation, monkeypatch, tmp_path):
    original = {"state": "finished", "review": {"checks": {"accepted": False}}}
    followup = {"state": "finished", "review": {"checks": {"accepted": True}}}
    (tmp_path / "live.json").write_text(json.dumps(original))
    (tmp_path / "followup.json").write_text(json.dumps(followup))
    def forbidden(*args, **kwargs):
        pytest.fail("Resume cannot load environment or access provider")
    monkeypatch.setattr(evaluation, "load_dotenv", forbidden)
    monkeypatch.setattr(evaluation, "ChatCompletionsSemanticProvider", forbidden)
    monkeypatch.setattr(sys, "argv", ["evaluate_memory_repairs", "--resume", "--followup", "inspect",
                                     "--output", str(tmp_path)])
    evaluation.main()
