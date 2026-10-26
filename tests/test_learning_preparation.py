import copy

import pytest
from test_cognition import STAMP, UNKNOWN_TIME, event, working
from test_consolidation_diversity import CROWDED_TEXTS, fragmented_data
from test_consolidation_planning import LONG_INSTANCE_TEXTS
from test_semantic import reference

from agent_memory.consolidation_planning import plan_consolidation, select_consolidation_candidates
from agent_memory.contracts import validate
from agent_memory.helpers import digest
from agent_memory.service import MemoryService

ORIGINAL_PLAN_HASHES = {
    1: (
        "3dd3cea9d756b822bad218323f72698d1db4b7ebcb0782e9da9a8db4bf2fec9f",
        "fc1aff0187df08b606efb54ac494591304c70b0bc3228b4b80206a57fecbb0e9",
        "b64548111f07b1a82cd1fccc4cde5d06c46d5a29d014f810aedca4c90fd82fdf",
        "0b7c5e895dacf75ef6fea94ea937e187d09b7bfed3401cfbe49a687d7a765672",
    ),
    2: (
        "95cf04db7746464af112c77c288829c98ad92732f2991b2e54de9bbabf7b785f",
        "fe01e92d118ca49a26ec3d5c8464c972c5b6edbdf2a179415ab7c19c5061ab36",
        "88c9d57bb4686488947104e40168fa7267f8935ae49ed6c8894fee43404d952b",
        "5656d2c22a5b613d7925c0595fa7b592080fa68456a66ed36cdac677f303946b",
    ),
    3: (
        "0b214c320ce9853d4c77bfd0d38c1bf611a30d5db79bd60255a7765c620bf074",
        "18a66f34fe3baf566792c426b1c8778d65710c932bb712e60f672d10691fbbaf",
        "6fbf789abd02711ea55e476eba686f130ad8a5ffcdacdd89c5f93bde90e498c5",
        "a2e4e98991798f7144a46be5b9719575ea43f40a28ca515fefd3ab0f8d9baaa5",
    ),
    4: (
        "c7ac798f5d326a4d49170459efa53398d4d369fe2bec6faba250b2c57b95fde6",
        "5058a8140c5221968f43ef47ef3965f601fa1f6a2deeb3da2489b063b2bfe95e",
        "5384f7fa1664a360ad345cfdf5377fbf123cbebc91919ef7e5d23d86d1a1be01",
        "23ee81c9cc7bd2f2b6d9b4be8a212735dbde430489131d6c6894c71500d014e6",
    ),
}


@pytest.mark.parametrize("version", ORIGINAL_PLAN_HASHES)
@pytest.mark.parametrize("restricted,suppress,index", [(False, False, 0), (False, True, 1),
                                                       (True, False, 2), (True, True, 3)])
def test_one_discovery_preserves_original_plan_bytes(version, restricted, suppress, index):
    data = fragmented_data((*CROWDED_TEXTS, *LONG_INSTANCE_TEXTS), (3, 3, 1, 1, 1, 1, 1))
    options = {"planner_version": f"independent-instance-comparison-v{version}"}
    candidates = plan_consolidation(data.sources, data.existing_assertions, include_all=True, **options)
    attempted = [item["objective_id"] for item in candidates["objectives"][:2]] if suppress else []
    original = copy.deepcopy(candidates)
    selected = select_consolidation_candidates(candidates, data.existing_assertions, attempted_ids=attempted)
    assert candidates == original
    if restricted:
        selected["objectives"] = []
    expected = ORIGINAL_PLAN_HASHES[version][index]
    assert digest(selected) == expected
    assert digest(plan_consolidation(data.sources, iter(data.existing_assertions),
                                    sampling={"publication_restricted": restricted},
                                    attempted_ids=attempted, **options)) == expected


def test_working_projection_keeps_later_evidence_available_and_does_not_mutate_snapshot():
    first, later = event("I plan to study."), event("I will book the room.", 2)
    state = working()
    state["items"] = [{"key": source["event_id"], "value": source["observed"]["content"]["text"],
                       "knowledge_state": "grounded", "evidence_refs": [reference(source)],
                       "expires_at": None, "updated_at": STAMP} for source in (first, later)]
    state["open_loops"] = [{"loop_id": "booking", "description": "Book the room", "state": "planned",
                            "due_time": UNKNOWN_TIME, "evidence_refs": [reference(later)],
                            "last_updated_at": STAMP}]
    validate("WorkingState", state)
    original = copy.deepcopy(state)
    service = MemoryService(None)
    prefix = service._working_from_events(state, [first])
    assert prefix["items"] == state["items"][:1] and prefix["open_loops"] == []
    complete = service._working_from_events(state, [first, later])
    assert complete == state == original
    stale = copy.deepcopy(later)
    stale["revision"] += 1
    assert service._working_from_events(state, [first, stale]) == prefix
    complete["items"][0]["value"] = "changed"
    assert state == original
