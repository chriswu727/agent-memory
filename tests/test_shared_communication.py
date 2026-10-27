import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_bounded_learning_semantics import publication_snapshot
from test_communication_views import run as communication_case
from test_semantic import verifier

from membrain import semantic
from membrain.consolidation import _check_result
from membrain.helpers import DomainError, digest
from membrain.source_composition import (
    COMMUNICATION_VIEW_INPUT_VERSION,
    SHARED_COMMUNICATION_INPUT_VERSION,
)

FIXTURE = Path(__file__).parent / 'fixtures/recorded_communication_verification_budget.json'


def replay(monkeypatch, version):
    recorded = json.loads(FIXTURE.read_text())
    assert digest(recorded['proposal_response']) == recorded['proposal_response_sha256']
    values = recorded['batch']
    data = semantic.CognitiveBatch(
        'offline-source-sharing', values['working_state']['scope'], tuple(values['sources']),
        **{key: values[key] for key in ('existing_assertions', 'working_state', 'episodes', 'sampling',
                                        'belief_relations', 'consolidation_plan', 'current_input_refs',
                                        'learning_input_refs')},
        proposal_mode=recorded['proposal_mode'], input_contract_version=version,
    )
    requests, sizes = [], []
    base = semantic.request_payload

    def measured(phase, *args, **kwargs):
        messages, size = base(phase, *args, **kwargs)
        sizes.append((phase, size))
        return messages, size

    def verify(request):
        requests.append(request)
        return verifier(request)

    monkeypatch.setattr(semantic, 'request_payload', measured)
    result = semantic.SemanticProcessor(lambda _: recorded['proposal_response'], verify,
                                        semantic.SemanticPolicy(**recorded['policy'])).process(
        data, reserve=lambda **_: None)
    return data, result, requests, sizes


def test_exact_recorded_general_verification_still_hits_frozen_v3_limit(monkeypatch):
    _, result, requests, sizes = replay(monkeypatch, COMMUNICATION_VIEW_INPUT_VERSION)
    assert result.status == 'budget_exhausted' and result.model_calls == 1 and not requests
    assert sizes == [('proposal', 53470), ('verification', 121913)]
    assert not result.claims and not result.episode_actions


def test_shared_sources_fit_original_budget_without_losing_per_reference_authority(monkeypatch):
    data, result, requests, sizes = replay(monkeypatch, SHARED_COMMUNICATION_INPUT_VERSION)
    assert result.status == 'completed' and result.model_calls == 2 and len(requests) == 1
    assert len(result.claims) == 8 and len(result.episode_actions) == 1
    assert all(size <= 98304 for _, size in sizes) and sum(size for _, size in sizes) <= 196608
    body = json.loads(requests[0].messages[-1]['content'])
    table = {(s['event_id'], s['revision']): s for s in body['verification_sources']}
    originals = {(s['event_id'], s['revision']): s for s in data.sources}
    assert table == originals and len(table) == 2
    count = 0
    for entry in body['proposals']:
        for evidence in entry['evidence']:
            assert 'source' not in evidence
            ref = evidence['reference']
            source = table[(evidence['source_ref']['event_id'], evidence['source_ref']['revision'])]
            assert source['event_id'] == ref['event_id'] and source['revision'] == ref['event_revision']
            assert source['content_sha256'] == ref['source_content_sha256']
            assert evidence['excerpt'] == source['observed']['content']['text'][ref['span']['start']:ref['span']['end']]
            count += 1
    assert count == 20
    assert 'shared-verification-sources-v1' in requests[0].messages[0]['content']
    _check_result(data, result, publication_snapshot(data))
    damaged = copy.deepcopy(result.claims)
    speech = next(item for item in damaged if item['claim_kind'] == 'agent_said')
    speech['statement'] = 'The assistant performed the actions inside its draft.'
    with pytest.raises(DomainError, match='Cognitive proposal is unsupported'):
        _check_result(data, replace(result, claims=damaged), publication_snapshot(data))


def test_already_shared_local_context_is_not_projected_a_second_time():
    data, result, requests, _, _ = communication_case(version=SHARED_COMMUNICATION_INPUT_VERSION)
    assert result.status == 'completed'
    body = json.loads(requests[0].messages[-1]['content'])
    assert len(body['local_input_context']) == 2 and 'verification_sources' not in body
    assert 'the assistant checked' not in result.claims[0]['statement']
    _check_result(data, result, publication_snapshot(data))
