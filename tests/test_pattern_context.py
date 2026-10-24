import copy
import json

import pytest
from test_cognition import STAMP, event, prepare_payload, request, scope, state, working
from test_consolidation import memory as memory
from test_contextual_recall import CONDITION, UNBOUNDED, observation
from test_patterns import version
from test_recall_control import decision
from test_recall_control import packet as controller_packet
from test_recall_control import run as control
from test_semantic import episode, proposals, run

from agent_memory import cognition
from agent_memory.contracts import validate
from agent_memory.helpers import digest
from agent_memory.patterns import build_patterns


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    monkeypatch.setattr(cognition, "now", lambda: STAMP)


def scenario(*, long=True, extra_root=False, conditional=False):
    common = "routine common practice " * (180 if long else 1) + "."
    sources = [event(common, 1), event("routine A had red tea.", 2),
               event(common, 3), event("routine B avoided tea and drank water.", 4)]
    memories = [cognition.extract_event(source)[0] for source in sources]
    for assertion in memories:
        assertion["representation"] = "episode_fragment"
    if conditional:
        memories[1].update(conditions=copy.deepcopy(CONDITION), valid_during=copy.deepcopy(UNBOUNDED))
        memories[3].update(conditions={"op": "not", "arg": copy.deepcopy(CONDITION)},
                           valid_during=copy.deepcopy(UNBOUNDED))
    episodes = [{
        "episode_id": "episode-" + name, "revision": 1, "scope": scope(), "state": "closed",
        "member_refs": [{"event_id": source["event_id"], "revision": source["revision"]}
                        for source in sources[start:start + 2]],
        "root_event_ids": [source["event_id"] for source in sources[start:start + 2]],
    } for name, start in (("a", 0), ("b", 2))]
    if extra_root:
        sources.append(event("The episode boundary was explicitly observed.", 5))
        episodes[0]["root_event_ids"].append(sources[-1]["event_id"])
    views = build_patterns(scope(), memories, episodes)
    assert len(views) == 1
    current = state(
        sources[-1]["sequence"], candidate_total=len(memories), pattern_views=views,
        working_event_metadata={source["event_id"]: source for source in sources},
    )
    return sources, memories, current


def prepare_case(sources, memories, current, *, payload=None, recall_changes=None):
    recalled = cognition.recall(scope(), request("routine", **(recall_changes or {})),
                               memories, sources, current)
    return cognition.prepare(scope(), payload or prepare_payload(), working(), recalled, current), recalled


def memory_blocks(packet):
    return [(block, json.loads(block["text"])) for block in packet["blocks"]
            if block["section"] in {"relevant_memory", "conditional_memory"}]


def assert_metered(packet):
    validate("ContextPacket", packet)
    assert packet["usage"]["context_tokens"] == cognition.estimate_tokens(cognition.render_context(packet))
    assert packet["usage"]["context_tokens"] <= packet["budget"]["max_context_tokens"]


def test_complete_selected_pattern_compresses_shared_meaning_without_mixing_instances():
    sources, memories, current = scenario()
    before = copy.deepcopy((memories, current))
    packet, recalled = prepare_case(sources, memories, current)
    baseline, _ = prepare_case(sources, memories, {**current, "pattern_views": []})
    blocks = memory_blocks(packet)
    assert len(blocks) == 1
    block, content = blocks[0]
    assert content["format"] == "schema-residual-v1"
    assert content["coverage"] == "selected_assertions_only"
    assert "does_not_fill_unobserved_details" in content["use"]
    assert content["definition"]["shared_claims"][0]["meaning"]["statement"] == memories[0]["statement"]
    for instance, actual in zip(content["instances"], (memories[:2], memories[2:]), strict=True):
        assert instance["observed_memory_versions"] == [version(memory) for memory in actual]
        assert instance["shared_bindings"][0]["memory_version"] == version(actual[0])
        assert instance["shared_bindings"][0]["evidence_refs"] == actual[0]["evidence_refs"]
        assert instance["residuals"][0]["fact"]["statement"] == actual[1]["statement"]
        assert instance["residuals"][0]["evidence_refs"] == actual[1]["evidence_refs"]
    assert {ref["event_id"] for ref in block["evidence_refs"]} == {source["event_id"] for source in sources}
    assert packet["usage"]["context_tokens"] < baseline["usage"]["context_tokens"]
    assert len(packet["recall_traces"]) == len(memories)
    for trace in packet["recall_traces"]:
        original = next(memory for memory in memories if memory["memory_id"] == trace["memory_id"])
        assert trace["evidence_refs"] == original["evidence_refs"]
    assert len(recalled["items"]) == 4
    assert (memories, current) == before
    assert_metered(packet)


@pytest.mark.parametrize("long", [False, True])
def test_complete_group_budget_never_keeps_common_facts_without_its_selected_residuals(long):
    sources, memories, current = scenario(long=long)
    full, _ = prepare_case(sources, memories, current)
    limited, _ = prepare_case(sources, memories, current,
                              payload=prepare_payload(tokens=full["usage"]["context_tokens"] - 1))
    assert memory_blocks(limited) == []
    assert limited["recall_traces"] == []
    assert limited["usage"]["stopped_by"] == "context_limit"
    assert json.loads(limited["blocks"][0]["text"])["context_truncated"] is True
    assert_metered(limited)


def test_short_common_meaning_falls_back_to_atomic_complete_facts_with_instance_identity():
    sources, memories, current = scenario(long=False)
    packet, _ = prepare_case(sources, memories, current)
    blocks = memory_blocks(packet)
    assert len(blocks) == len(memories)
    assert all("format" not in content for _, content in blocks)
    for _, content in blocks:
        memory = next(memory for memory in memories if version(memory) == content["memory_version"])
        assert content["statement"] == memory["statement"]
        assert content["representation"] == "episode_fragment"
        assert content["coverage"] == "selected_assertions_only"
        suffix = "a" if memory in memories[:2] else "b"
        assert content["episode_refs"] == [{"episode_id": "episode-" + suffix, "revision": 1}]
    assert_metered(packet)


@pytest.mark.parametrize("change", ["missing_member", "wrong_revision", "same_revision_changed", "wrong_scope"])
def test_incomplete_or_changed_group_falls_back_without_reconstructing_an_unselected_fact(change):
    sources, memories, current = scenario()
    if change == "missing_member":
        memories.pop()
    elif change == "wrong_revision":
        memories[0]["revision"] += 1
    elif change == "same_revision_changed":
        memories[0]["statement"] += " changed interpretation"
    else:
        view = current["pattern_views"][0]
        view["scope"]["branch_id"] = "other"
        view["view_id"] = "pattern-" + digest({key: value for key, value in view.items() if key != "view_id"})
    packet, recalled = prepare_case(sources, memories, current)
    assert len(memory_blocks(packet)) == len(recalled["items"])
    assert all("format" not in content and "episode_refs" not in content for _, content in memory_blocks(packet))
    assert {content["statement"] for _, content in memory_blocks(packet)} == {memory["statement"] for memory in memories}


def test_pattern_never_adds_facts_removed_by_the_history_boundary():
    sources, memories, current = scenario()
    packet, recalled = prepare_case(sources, memories, current, recall_changes={
        "evidence_mode": "prior_record_check", "evidence_cutoff": {"mode": "through_sequence", "through_sequence": 2},
    })
    assert len(recalled["items"]) == len(memory_blocks(packet)) == 2
    assert all("format" not in content for _, content in memory_blocks(packet))
    assert memories[3]["statement"] not in cognition.render_context(packet)


@pytest.mark.parametrize("change", ["missing", "future", "cue_descendant", "superseded"])
def test_extra_organization_roots_respect_availability_and_history_bounds(change):
    sources, memories, current = scenario(extra_root=True)
    recall_changes = {}
    if change == "missing":
        current["working_event_metadata"].pop("event-5")
    elif change == "superseded":
        current["superseded_evidence_refs"] = cognition.extract_event(sources[-1])[0]["evidence_refs"]
    else:
        recall_changes = {"evidence_mode": "prior_record_check",
                          "evidence_cutoff": {"mode": "through_sequence", "through_sequence": 4}}
        if change == "cue_descendant":
            cue = event("Do you remember the routine?", 6)
            sources.append(cue)
            current.update(durable_sequence=6, indexed_sequence=6)
            current["working_event_metadata"]["event-6"] = cue
            sources[4]["observed"]["parent_source_event_keys"] = [cue["observed"]["source_event_key"]]
            recall_changes.update(query_event_ids=[cue["event_id"]],
                                  evidence_cutoff={"mode": "through_sequence", "through_sequence": 5})
    packet, recalled = prepare_case(sources, memories, current, recall_changes=recall_changes)
    assert len(recalled["items"]) == len(memory_blocks(packet)) == 4
    assert all("format" not in content for _, content in memory_blocks(packet))
    assert all(ref["event_id"] != "event-5" for block in packet["blocks"] for ref in block["evidence_refs"])


def test_extra_organization_root_is_bound_as_context_without_becoming_a_claim():
    sources, memories, current = scenario(extra_root=True)
    packet, recalled = prepare_case(sources, memories, current)
    block, content = memory_blocks(packet)[0]
    assert content["format"] == "schema-residual-v1"
    root = [ref for ref in block["evidence_refs"] if ref["event_id"] == "event-5"]
    assert len(root) == 1 and root[0]["relationship"] == "context_only"
    assert all(ref["event_id"] != "event-5" for item in recalled["items"] for ref in item["memory"]["evidence_refs"])
    assert sources[-1]["observed"]["content"]["text"] not in cognition.render_context(packet)


def test_each_instance_keeps_its_own_applicability_and_context_proof_sources():
    sources, memories, current = scenario(conditional=True)
    observed = event("我正在备考。", 5)
    sources.append(observed)
    current.update(durable_sequence=5, indexed_sequence=5,
                   context_observations=[observation(observed)])
    current["working_event_metadata"][observed["event_id"]] = observed
    packet, _ = prepare_case(sources, memories, current)
    block, content = memory_blocks(packet)[0]
    assert content["format"] == "schema-residual-v1"
    assert block["section"] == "conditional_memory"
    first, second = [instance["residuals"][0]["fact"] for instance in content["instances"]]
    assert first["applicability"]["value"] == "true"
    assert second["applicability"]["value"] == "false"
    assert first["use"] == "historical_report_not_current_rule"
    assert second["use"] == "historical_report_not_current_rule"
    assert {ref["event_id"] for ref in block["evidence_refs"]} == {source["event_id"] for source in sources}
    assert_metered(packet)


def test_unfrozen_recall_plan_uses_original_facts_even_when_a_pattern_is_available():
    sources, memories, current = scenario()
    payload = prepare_payload()
    payload.update(expected_dependency_generation=0, evidence_mode="prior_record_check",
                   evidence_cutoff={"mode": "through_sequence", "through_sequence": 4})
    payload["recall_plan"] = {
        "version": "evidence-recall-v1", "memory_versions": [version(memory) for memory in memories],
        "recent_event_refs": [],
        "observed_event_refs": [{"event_id": source["event_id"], "revision": 1} for source in sources],
        "outcome": "sufficient", "model_calls": 1, "search_calls": 0,
        "input_bytes": 100, "elapsed_ms": 1, "trace": [], "coverage_limitations": [],
    }
    packet, _ = prepare_case(sources, memories, current, payload=payload, recall_changes={
        "evidence_mode": "prior_record_check", "evidence_cutoff": payload["evidence_cutoff"],
    })
    assert len(memory_blocks(packet)) == 4
    assert all("format" not in content and "episode_refs" not in content for _, content in memory_blocks(packet))


def test_recall_controller_preserves_representation_conditions_and_counterexample_dependencies():
    sources, memories, _ = scenario(long=False, conditional=True)
    candidate = copy.deepcopy(memories[1])
    candidate["representation"] = "schema"
    candidate["counterexamples"] = [{**memories[3]["evidence_refs"][0], "relationship": "contradicts"}]
    reservations = []

    def reason(request):
        view = json.loads(request.messages[1]["content"])
        shown = view["candidates"][0]
        assert shown["representation"] == "schema"
        assert shown["conditions"] == candidate["conditions"]
        assert shown["valid_during"] == candidate["valid_during"]
        assert [view["sources"][identifier] for identifier in shown["counterexamples"]] == [
            {key: value for key, value in reference.items() if key != "source_content_sha256"}
            for reference in candidate["counterexamples"]
        ]
        assert "Never combine details from distinct episodes" in request.messages[0]["content"]
        return decision(selected=[candidate["memory_id"]])

    plan = control(reason, initial=controller_packet(candidate),
                   reserve=lambda **values: reservations.append(values))
    assert plan["outcome"] == "sufficient"
    assert {ref["event_id"] for ref in plan["observed_event_refs"]} == {"recent", "event-2", "event-4"}
    assert reservations[0]["source_refs"] == plan["observed_event_refs"]


@pytest.mark.integration
@pytest.mark.parametrize("extra_root", [False, True])
def test_service_prepare_loads_verified_patterns_binds_extra_roots_and_rejects_corrected_views(memory, extra_root):
    sources = []
    hidden = None
    for index, detail in enumerate(("routine A had red tea.", "routine B avoided tea and drank water.")):
        common = memory.observe("routine common practice " * 180 + ".")
        specific = memory.observe(detail)
        inputs = [common, specific]
        if extra_root and index == 0:
            hidden = memory.observe("The episode boundary was explicitly observed.")
            inputs.append(hidden)
        batch = memory.batch(*inputs)
        action = episode(common, member_refs=[{"event_id": source["event_id"], "revision": 1}
                                             for source in (common, specific)])
        memory.publish(batch, run(batch, proposals(episodes=[action])))
        sources.extend(inputs)
    assertions = [row["document"] for row in memory.rows("memory_assertions")]
    episodes = [row["document"] for row in memory.rows("memory_episodes")]
    batch = memory.batch(*sources, existing_assertions=tuple(assertions), episodes=tuple(episodes))
    assert memory.publish(batch, run(batch, proposals()))["pattern_view_ids"]
    latest = memory.observe("routine")
    payload = prepare_payload()
    payload["latest_input_event_ids"] = [latest["event_id"]]
    packet = memory.action("prepare", payload)
    compressed = [(block, content) for block, content in memory_blocks(packet)
                  if content.get("format") == "schema-residual-v1"]
    assert len(compressed) == 1
    assert len(compressed[0][1]["instances"]) == 2
    if hidden:
        refs = [ref for ref in compressed[0][0]["evidence_refs"] if ref["event_id"] == hidden["event_id"]]
        assert len(refs) == 1 and refs[0]["relationship"] == "context_only"
        assert hidden["observed"]["content"]["text"] not in cognition.render_context(packet)
    assert_metered(packet)
    memory.observe("The routine changed completely.", key=sources[0]["observed"]["source_event_key"], revision=2)
    corrected = memory.action("prepare", payload)
    assert not any(content.get("format") == "schema-residual-v1" for _, content in memory_blocks(corrected))
    assert "routine B avoided tea and drank water" in cognition.render_context(corrected)
