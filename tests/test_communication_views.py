import copy
import json
from dataclasses import replace

import pytest
from test_bounded_learning_semantics import local_batch, local_claim, publication_snapshot
from test_cognition import event
from test_semantic import encoded, episode, proposals, verifier

from membrain import cognition
from membrain.consolidation import _check_result
from membrain.helpers import DomainError, digest
from membrain.semantic import SemanticProcessor
from membrain.source_composition import (
    COMMUNICATION_VIEW_INPUT_VERSION,
    COMPOSITION_V2_INPUT_VERSION,
)

DRAFT = (
    'Here’s a more casual version:\n\n“Sam exported the Cedar booklet, I checked the captions, '
    'and Priya was the one who uploaded the files — not Sam. '
    'I still haven’t heard back from the recipient that they’ve got them.”'
)
WRONG_CLAIM = (
    'The assistant delivered a more casual version of the account, saying that Sam exported '
    'the Cedar booklet, the assistant checked the captions, Priya uploaded the files instead '
    'of Sam, and the assistant had not yet heard back from the recipient that they got them.'
)
WRONG_EPISODE = (
    'The user asked for a more casual revision while keeping the actors clear; the assistant '
    'provided a revised casual version identifying Sam as the exporter, the assistant as '
    'the caption checker, and Priya as the uploader.'
)


def run(text=DRAFT, *, version=COMMUNICATION_VIEW_INPUT_VERSION, mutate=None):
    user = event('Make that a little more casual, but keep who did what clear.')
    assistant = event(text, sequence=2, role='assistant')
    data = local_batch(user, assistant, input_contract_version=version)
    item = local_claim(assistant, claim_kind='agent_said', statement=WRONG_CLAIM,
                       world=cognition._world(assistant['observed'], text))
    organization = episode(user, organization_statement=WRONG_EPISODE)
    organization['member_refs'].append({'event_id': assistant['event_id'], 'revision': 1})
    if mutate:
        mutate(item)
    raw = encoded(proposals(item, episodes=[organization]))
    calls, operations = [], []

    def verify(request):
        calls.append(request)
        return verifier(request)

    result = SemanticProcessor(lambda _: raw, verify).process(
        data, reserve=lambda **_: None, on_call_result=lambda **value: operations.append(value))
    return data, result, calls, operations, raw


def test_recorded_false_positive_cannot_publish_normalized_draft_actors_even_with_all_supports():
    data, result, calls, operations, raw = run()
    assert result.status == 'completed' and result.model_calls == 2
    assert len(result.claims) == len(result.episode_actions) == 1
    statement = result.claims[0]['statement']
    assert json.loads(statement.split(': ', 1)[1]) == DRAFT
    assert 'the assistant checked' not in statement
    organization = result.episode_actions[0]['organization_statement']
    assert json.dumps(DRAFT, ensure_ascii=False) in organization
    assert 'the assistant as the caption checker' not in organization
    verified = json.loads(calls[0].messages[-1]['content'])['proposals']
    assert verified[0]['proposal']['statement'] == statement
    assert verified[1]['proposal']['organization_statement'] == organization
    assert operations[0]['response_fingerprint'] == digest(raw)
    assert WRONG_CLAIM in raw and WRONG_EPISODE in raw
    _check_result(data, result, publication_snapshot(data))


def test_frozen_v2_contract_and_raw_sources_are_not_rewritten():
    data, old, _, _, _ = run(version=COMPOSITION_V2_INPUT_VERSION)
    assert old.status == 'completed' and old.claims[0]['statement'] == WRONG_CLAIM
    assert old.episode_actions[0]['organization_statement'] == WRONG_EPISODE
    _, current, _, _, _ = run()
    assert current.claims[0]['statement'] != WRONG_CLAIM
    assert data.sources[1]['observed']['content']['text'] == DRAFT


@pytest.mark.parametrize('text', [
    'A draft without quotation marks: I checked the captions.',
    '版本如下：我检查了字幕；文件是 Priya 上传的。',
    'Example:\n> I checked the captions.\n\nI can revise it.',
    'print("I checked the captions")',
    'She wrote “I will help you” and he replied “I cannot”.',
])
def test_communication_view_preserves_nested_and_unmarked_voice_without_language_heuristics(text):
    _, result, _, _, _ = run(text)
    assert result.status == 'completed'
    assert json.loads(result.claims[0]['statement'].split(': ', 1)[1]) == text


def test_generated_short_view_cannot_reintroduce_the_wrong_actor():
    _, result, calls, _, _ = run(mutate=lambda item: item.update(
        recall_summary={'statement': 'The assistant checked the captions.', 'detail_cues': ['captions']}))
    assert result.status == 'completed' and 'recall_summary' not in result.claims[0]
    assert 'recall_summary' not in json.loads(calls[0].messages[-1]['content'])['proposals'][0]['proposal']


def test_complete_overlong_quote_is_not_clipped_and_episode_uses_neutral_membership():
    text = 'I checked captions. ' * 150
    data, result, _, _, _ = run(text)
    assert result.status == 'partial' and not result.claims
    assert result.rejected == ({'proposal_id': 'claim-1', 'reason': 'invalid_proposal'},)
    assert len(result.episode_actions) == 1
    statement = result.episode_actions[0]['organization_statement']
    assert '1 assistant source record(s)' in statement and '1 user source record(s)' in statement
    assert 'captions' not in statement and 'external actions and completion are not established' in statement
    assert data.sources[1]['observed']['content']['text'] == text
    _check_result(data, result, publication_snapshot(data))


@pytest.mark.parametrize('damage', ['statement', 'summary', 'episode', 'citation'])
def test_publication_recomputes_exact_view_instead_of_trusting_positive_verdicts(damage):
    data, result, _, _, _ = run()
    claims = copy.deepcopy(result.claims)
    episodes = copy.deepcopy(result.episode_actions)
    if damage == 'statement':
        claims[0]['statement'] = WRONG_CLAIM
    elif damage == 'summary':
        claims[0]['recall_summary'] = {'statement': 'The assistant checked captions.', 'detail_cues': []}
    elif damage == 'episode':
        episodes[0]['organization_statement'] = WRONG_EPISODE
    else:
        claims[0]['evidence_refs'][0]['span']['start'] = 31
    with pytest.raises(DomainError, match='Cognitive proposal is unsupported'):
        _check_result(data, replace(result, claims=claims, episode_actions=episodes), publication_snapshot(data))


def test_stale_hash_never_becomes_authoritative_quoted_text():
    _, result, calls, _, _ = run(mutate=lambda item: item['evidence_refs'][0].update(
        source_content_sha256='a' * 64))
    assert not result.claims and result.rejected[0]['reason'] == 'invalid_proposal'
    assert all(entry['group'] != 'claims' for entry in json.loads(calls[0].messages[-1]['content'])['proposals'])


def test_user_fact_and_personal_short_view_still_use_model_selected_semantic_encoding():
    source = event('My aunt laid out the cloth. I stitched the seam. Being trusted made me feel capable.')
    data = local_batch(source, input_contract_version=COMMUNICATION_VIEW_INPUT_VERSION)
    item = local_claim(source, statement='The user reported stitching a seam and feeling capable when trusted.',
                       recall_summary={'statement': 'The user felt capable when trusted.', 'detail_cues': ['seam']})
    def verify(request):
        response = json.loads(verifier(request))
        response['assessments'][0]['recall_summary_verdict'] = 'supports'
        return encoded(response)

    result = SemanticProcessor(lambda _: encoded(proposals(item)), verify).process(data, reserve=lambda **_: None)
    assert result.status == 'completed' and result.claims[0]['statement'] == item['statement']
    assert result.claims[0]['recall_summary'] == item['recall_summary']
    _check_result(data, result, publication_snapshot(data))
