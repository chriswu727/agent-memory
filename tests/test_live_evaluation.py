import copy
from pathlib import Path
from unittest.mock import patch

import pytest
from test_belief_recall import relation
from test_belief_semantic import link_verifier
from test_cognition import event, prepare_payload, request, scope, state, working
from test_contextual_recall import CONDITION
from test_semantic import batch, claim, proposals, reference, run

from membrain import cognition
from membrain.consolidation import _assertion
from membrain.consolidation_planning import plan_consolidation

with pytest.MonkeyPatch.context() as imports:
    imports.syspath_prepend(str(Path(__file__).parents[1]))
    from examples.evaluate_deepseek import EVALUATION_TIMES, SCENARIOS, stage_checks


def version(memory):
    return {"memory_id": memory["memory_id"], "revision": memory["revision"]}


def interval(start, end=None):
    return {"kind": "interval", "start": {"kind": "instant", "at": start},
            "end": {"kind": "instant", "at": end} if end else {"kind": "unbounded"}}


def semantic(source, statement, validity, **changes):
    data = batch(source)
    result = run(data, proposals(claim(source, statement=statement, conditions={"op": "always"}, valid_during=validity, **changes)))
    return _assertion(result.claims[0], data, {"dependency_generation": 0}, {"root_event_ids": [source["event_id"]]})


def temporal_stages():
    sources, memories, relations, stages = [], [], [], []
    meanings = ("用户的一般回复偏好是简短回答。", "用户在这一天偏好详细解释。", "用户长期偏好先给结论，再解释理由。")
    intervals = (interval("2026-10-20T00:00:00Z"), interval("2026-10-24T00:00:00Z", "2026-10-25T00:00:00Z"),
                 interval("2026-10-26T00:00:00Z"))
    for index in range(3):
        source = event(SCENARIOS["temporal"][index][0], index + 1)
        memory = semantic(source, meanings[index], intervals[index])
        sources.append(source)
        memories.append(memory)
        added = []
        if index:
            link = relation(memory, memories[:1], kind="exception" if index == 1 else "supersedes",
                            valid_during=intervals[index], evidence_refs=copy.deepcopy(
                                memory["evidence_refs"] + memories[0]["evidence_refs"]))
            relations.append(link)
            added.append({"relation_id": link["relation_id"], "revision": 1})
        current = state(index + 1, candidate_total=len(memories), belief_relations=relations,
                        working_event_metadata={value["event_id"]: value for value in sources})
        observations = []
        for at in EVALUATION_TIMES:
            with patch("membrain.cognition.now", return_value=at):
                recalled = cognition.recall(scope(), request("回复偏好 简短 详细解释 结论 理由"), memories, sources, current)
                packet = cognition.prepare(scope(), prepare_payload(), working(), recalled, current)
            observations.append({"at": at, "items": recalled["items"], "blocks": packet["blocks"]})
        stages.append(copy.deepcopy({"input_event_ids": [source["event_id"]],
            "job": {"state": "completed", "result": {"status": "completed", "publication": {
                "memory_versions": [version(memory)], "belief_relation_versions": added},
                "learning_window": {"processing_succeeded": True},
                "learning_input_refs": [{"event_id": source["event_id"], "revision": 1}]}},
            "documents": {"memories": memories, "relations": relations}, "recall": observations}))
    return stages


@pytest.fixture
def stages():
    return temporal_stages()


@pytest.mark.parametrize("index", [0, 1, 2])
def test_fixture_oracle_accepts_actual_recall_and_prepare_document_shapes(stages, index):
    checks = stage_checks("temporal", index, stages[index], stages[index - 1] if index else None)
    assert checks and all(checks.values()), checks


def test_unfamiliar_condition_requires_an_actual_published_preference(stages):
    checks = stage_checks("conditional", 2, stages[2], stages[1])
    assert checks["unfamiliar_condition_not_coerced"] is False


@pytest.mark.parametrize("damage", ["no_job_result", "no_publication", "missing_document", "capture_only",
                                    "unrelated_source", "no_processed_inputs", "no_recall", "no_items", "no_prepare",
                                    "missing_time", "wrong_applicability", "wrong_section", "wrong_validity"])
def test_baseline_cannot_pass_from_empty_or_nonsemantic_or_inapplicable_results(stages, damage):
    stage = stages[0]
    result = stage["job"]["result"]
    memory = stage["documents"]["memories"][0]
    if damage == "no_job_result":
        stage["job"]["result"] = None
    elif damage == "no_publication":
        result["publication"]["memory_versions"] = []
    elif damage == "missing_document":
        stage["documents"]["memories"] = []
    elif damage == "capture_only":
        memory["derivation"]["method"] = "direct_capture"
    elif damage == "unrelated_source":
        memory["evidence_refs"][0]["event_id"] = "not-this-input"
    elif damage == "no_processed_inputs":
        result["learning_input_refs"] = []
    elif damage == "no_recall":
        stage["recall"] = []
    elif damage == "no_items":
        stage["recall"][0]["items"] = []
    elif damage == "no_prepare":
        stage["recall"][0]["blocks"] = []
    elif damage == "missing_time":
        stage["recall"].pop()
    elif damage == "wrong_applicability":
        stage["recall"][0]["items"][0]["applicability"]["value"] = "false"
    elif damage == "wrong_section":
        for block in stage["recall"][0]["blocks"]:
            if block["section"] == "relevant_memory":
                block["section"] = "conditional_memory"
    else:
        memory["valid_during"]["start"]["at"] = "2026-10-21T00:00:00Z"
    checks = stage_checks("temporal", 0, stage, None)
    assert not all(checks.values()), checks


@pytest.mark.parametrize("damage", ["no_previous", "changed_old_condition", "reversed_direction", "unpublished_source",
                                    "wrong_target_revision", "wrong_relation_revision", "missing_old_evidence",
                                    "wrong_relation_time", "wrong_old_resolution", "incomplete_relation",
                                    "exception_still_applies_after_expiry", "all_results_match_the_wrong_expiry"])
def test_exception_oracle_rejects_wrong_direction_provenance_and_temporal_resolution(stages, damage):
    previous, stage = stages[:2]
    link = stage["documents"]["relations"][0]
    old, new = stage["documents"]["memories"]
    if damage == "no_previous":
        previous = None
    elif damage == "changed_old_condition":
        old["conditions"] = {"op": "unresolved", "label": "edited old condition"}
    elif damage == "reversed_direction":
        link["source_memory_version"], link["target_memory_versions"] = version(old), [version(new)]
    elif damage == "unpublished_source":
        stage["job"]["result"]["publication"]["memory_versions"] = [version(old)]
    elif damage == "wrong_target_revision":
        link["target_memory_versions"][0]["revision"] = 2
    elif damage == "wrong_relation_revision":
        stage["job"]["result"]["publication"]["belief_relation_versions"][0]["revision"] = 2
    elif damage == "missing_old_evidence":
        link["evidence_refs"] = copy.deepcopy(new["evidence_refs"])
    elif damage == "wrong_relation_time":
        link["valid_during"]["end"]["at"] = "2026-10-28T00:00:00Z"
    elif damage == "all_results_match_the_wrong_expiry":
        link["valid_during"]["end"]["at"] = "2026-10-28T00:00:00Z"
        new["valid_during"] = copy.deepcopy(link["valid_during"])
        for observed in stage["recall"]:
            for item in observed["items"]:
                if version(item["memory"]) == version(old):
                    item["belief_resolution"]["state"] = "exception_applies"
                if version(item["memory"]) == version(new):
                    item["applicability"]["value"] = "true"
    else:
        observation = stage["recall"][1 if damage == "exception_still_applies_after_expiry" else 0]
        item = next(item for item in observation["items"] if version(item["memory"]) == version(old))
        if damage == "incomplete_relation":
            item["belief_resolution"]["complete"] = False
        else:
            item["belief_resolution"]["state"] = "exception_applies" if damage == "exception_still_applies_after_expiry" else "unchanged"
    checks = stage_checks("temporal", 1, stage, previous)
    assert not all(checks.values()), checks


def make_stage(sources, memories, links, current_inputs, published, new_links=()):
    current = state(len(sources), candidate_total=len(memories), belief_relations=links,
                    working_event_metadata={source["event_id"]: source for source in sources})
    observations = []
    for at in EVALUATION_TIMES:
        with patch("membrain.cognition.now", return_value=at):
            recalled = cognition.recall(scope(), request("建议 倾听 备考 复习 散步 记录"), memories, sources, current)
            packet = cognition.prepare(scope(), prepare_payload(), working(), recalled, current)
        observations.append({"at": at, "items": recalled["items"], "blocks": packet["blocks"]})
    return copy.deepcopy({"input_event_ids": [source["event_id"] for source in current_inputs],
        "job": {"state": "completed", "result": {"status": "completed", "publication": {
            "memory_versions": [version(memory) for memory in published],
            "belief_relation_versions": [{"relation_id": link["relation_id"], "revision": 1} for link in new_links]},
            "learning_window": {"processing_succeeded": True},
            "learning_input_refs": [{"event_id": source["event_id"], "revision": 1} for source in current_inputs]}},
        "documents": {"memories": memories, "relations": links}, "recall": observations})


def conditional_stages():
    sources = [event(SCENARIOS["conditional"][index][0], index + 1) for index in (0, 1)]
    old = semantic(sources[0], "用户平常喜欢直接得到建议。", interval("2026-10-20T00:00:00Z"))
    preference = reference(sources[1])
    preference["span"]["end"] = sources[1]["utterance_segments"][1]["span"]["end"]
    new = semantic(sources[1], "用户备考时希望先被倾听，不主动获得建议。", interval("2026-10-21T00:00:00Z"),
                   evidence_refs=[preference])
    new["conditions"] = copy.deepcopy(CONDITION)
    link = relation(new, [old], kind="exception", conditions=copy.deepcopy(CONDITION), valid_during=new["valid_during"],
                    evidence_refs=copy.deepcopy(old["evidence_refs"] + new["evidence_refs"]))
    return [make_stage(sources[:1], [old], [], sources[:1], [old]),
            make_stage(sources, [old, new], [link], sources[1:], [new], [link])]


@pytest.mark.parametrize("index", [0, 1])
def test_conditional_oracle_accepts_unknown_current_condition_without_inventing_observation(index):
    stages = conditional_stages()
    checks = stage_checks("conditional", index, stages[index], stages[index - 1] if index else None)
    assert all(checks.values()), checks


@pytest.mark.parametrize("damage", ["unconditional", "wrong_registered_concept", "assumed_active"])
def test_conditional_oracle_rejects_erased_or_coerced_or_assumed_condition(damage):
    previous, stage = conditional_stages()
    link = stage["documents"]["relations"][0]
    if damage == "unconditional":
        link["conditions"] = {"op": "always"}
    elif damage == "wrong_registered_concept":
        link["conditions"]["value"]["id"] = "activity:work"
    else:
        old = stage["documents"]["memories"][0]
        for observation in stage["recall"]:
            for item in observation["items"]:
                if version(item["memory"]) == version(old):
                    item["belief_resolution"]["state"] = "exception_applies"
    assert not all(stage_checks("conditional", 1, stage, previous).values())


def abstraction_stages():
    sources = [event(text, index + 1) for index, text in enumerate(SCENARIOS["abstraction"][0])]
    old = [semantic(source, "复习前散步的独立记录。" + str(index), {"kind": "unknown"},
                    memory_kind="episodic", claim_kind="reported_fact") for index, source in enumerate(sources)]
    previous = make_stage(sources, old, [], sources, old)
    cue = event(SCENARIOS["abstraction"][1][0], 3)
    data = batch(*sources, cue, existing_assertions=tuple(old))
    proposed = claim(sources[0], statement="两次记录中，复习前都进行了散步。", memory_kind="semantic",
                     representation="schema", claim_kind="inferred_pattern", conditions={"op": "always"},
                     valid_during={"kind": "unknown"}, evidence_refs=sum([copy.deepcopy(memory["evidence_refs"]) for memory in old], []),
                     source_memory_versions=[version(memory) for memory in old])
    planned = plan_consolidation(data.sources, data.existing_assertions)
    belief_link = {"proposal_id": "link-abstract", "kind": "abstracts",
                   "claim_proposal_id": proposed["proposal_id"], "target_memory_versions": [version(item) for item in old],
                   "evidence_refs": copy.deepcopy(proposed["evidence_refs"]), "conditions": {"op": "always"},
                   "valid_during": {"kind": "unknown"}, "world": "real_world"}
    result = run(data, {**proposals(proposed), "belief_links": [belief_link], "consolidation_decisions": [
        {"objective_id": objective["objective_id"], "outcome": "proposed", "reason": None,
         "link_proposal_ids": [belief_link["proposal_id"]]} for objective in planned["objectives"]
    ]}, verify=link_verifier)
    new = _assertion(result.claims[0], data, {"dependency_generation": 0},
                     {"root_event_ids": [source["event_id"] for source in [*sources, cue]]})
    link = relation(new, old, kind="abstracts", valid_during={"kind": "unknown"},
                    evidence_refs=copy.deepcopy(new["evidence_refs"]))
    stage = make_stage([*sources, cue], [*old, new], [link], [cue], [new], [link])
    return previous, stage


@pytest.mark.parametrize("index", [0, 1])
def test_abstraction_oracle_retains_independent_instances_and_does_not_require_cue_as_support(index):
    stages = abstraction_stages()
    checks = stage_checks("abstraction", index, stages[index], stages[index - 1] if index else None)
    assert all(checks.values()), checks


@pytest.mark.parametrize("damage", ["no_abstract_recall", "one_parent", "shared_source", "cue_as_support", "universal_statement"])
def test_abstraction_oracle_rejects_missing_independence_and_unfounded_generalization(damage):
    previous, stage = abstraction_stages()
    left, right, abstract = stage["documents"]["memories"]
    if damage == "no_abstract_recall":
        for observation in stage["recall"]:
            observation["items"] = []
    elif damage == "one_parent":
        abstract["derivation"]["source_memory_versions"].pop()
    elif damage == "shared_source":
        right["evidence_refs"] = copy.deepcopy(left["evidence_refs"])
    elif damage == "cue_as_support":
        abstract["evidence_refs"][0]["event_id"] = stage["input_event_ids"][0]
    else:
        abstract["statement"] = "用户每次复习前总是散步。"
    assert not all(stage_checks("abstraction", 1, stage, previous).values())


def test_oracle_compares_equivalent_timestamp_encodings_as_instants(stages):
    stage = stages[1]
    new = stage["documents"]["memories"][1]
    link = stage["documents"]["relations"][0]
    for value in (new["valid_during"], link["valid_during"]):
        for bound in ("start", "end"):
            value[bound]["at"] = value[bound]["at"].replace("Z", "+00:00")
    checks = stage_checks("temporal", 1, stage, stages[0])
    assert all(checks.values()), checks


def test_reported_job_completion_does_not_mask_noncompleted_processing(stages):
    stages[0]["job"]["result"]["status"] = "provider_error"
    assert not all(stage_checks("temporal", 0, stages[0], None).values())


def test_prior_statement_equality_does_not_allow_silent_source_or_time_mutation(stages):
    stages[1]["documents"]["memories"][0]["valid_during"] = {"kind": "unknown"}
    checks = stage_checks("temporal", 1, stages[1], stages[0])
    assert checks["prior_semantics_preserved"] is False


@pytest.mark.parametrize("polarity,accepted", [("positive", True), ("negative", False)])
def test_registered_condition_oracle_respects_optional_polarity(polarity, accepted):
    previous, stage = conditional_stages()
    stage["documents"]["relations"][0]["conditions"]["polarity"] = polarity
    stage["documents"]["memories"][1]["conditions"]["polarity"] = polarity
    checks = stage_checks("conditional", 1, stage, previous)
    assert checks["registered_condition_preserved"] is accepted
    assert all(checks.values()) is accepted
