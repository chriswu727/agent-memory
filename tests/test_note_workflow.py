import hashlib

import pytest
from test_timeline import Brain

from membrain.config import Settings
from membrain.helpers import DomainError, Principal, uid
from membrain.notes import NOTES_CONVERSATION, store_note
from membrain.service import MemoryService
from membrain.storage import Database

pytestmark = pytest.mark.integration


@pytest.fixture
def brain():
    try:
        settings = Settings.from_env()
    except ValueError:
        pytest.skip("MEMORY_DATABASE_URL must point to the dedicated test database")
    return Brain(MemoryService(Database(settings.database_url)), Principal(uid(), uid()))


def write(brain, text, **fields):
    return store_note(brain.service, brain.principal, brain.brain, {"request_id": uid(), "text": text, **fields})


def test_note_sources_survive_correction_and_erasure_reaches_the_derived_note(brain):
    source = brain.say("My passport appointment is on November 4.")
    first = write(brain, "Passport appointment on November 4.", source_event_ids=[source], kind="plan",
                  status="scheduled", occurs_at="2026-11-04")
    corrected = write(brain, "Passport appointment moved to November 5.", replaces=[first["note_id"]],
                      kind="plan", status="scheduled", occurs_at="2026-11-05", role="user")
    notes = brain.timeline(roles=("user", "assistant"), conversation_id=NOTES_CONVERSATION)["items"]
    assert [item["event_id"] for item in notes] == [corrected["note_id"]]
    assert notes[0]["source_event_ids"] == [source]
    assert notes[0]["actor_role"] == "user"
    generation = brain.service.dashboard(brain.principal, brain.brain)["brain"]["scope_generation"]
    brain.action("forget", {"idempotency_key": uid(), "selector": {"kind": "events", "ids": [source]},
                            "mode": "erase_content", "expected_scope_generation": generation})
    assert brain.timeline(roles=("user", "assistant"), conversation_id=NOTES_CONVERSATION)["items"] == []


def test_note_write_retries_are_idempotent_and_cannot_change_content(brain):
    arguments = {"request_id": uid(), "text": "Prefers short replies."}
    first = store_note(brain.service, brain.principal, brain.brain, arguments)
    assert store_note(brain.service, brain.principal, brain.brain, arguments) == first
    with pytest.raises(DomainError, match="different content"):
        store_note(brain.service, brain.principal, brain.brain, {**arguments, "text": "Other content."})
    assert len(brain.timeline(roles=("assistant",), conversation_id=NOTES_CONVERSATION)["items"]) == 1


def test_a_note_cannot_claim_sources_from_another_brain_and_failure_is_atomic(brain):
    foreign = Brain(brain.service, brain.principal)
    source = foreign.say("Another person's confidential appointment.")
    with pytest.raises(DomainError):
        write(brain, "An appointment.", source_event_ids=[source])
    assert brain.timeline(roles=("assistant",), conversation_id=NOTES_CONVERSATION)["items"] == []


def test_note_replacement_cannot_suppress_original_messages_and_scrubs_secrets(brain):
    message = brain.say("Original user message.")
    with pytest.raises(DomainError):
        write(brain, "An invalid replacement.", replaces=[message])
    write(brain, "password=neverstorethis <private>confidential</private> Keep the appointment.")
    assert any(item["event_id"] == message for item in brain.timeline()["items"])
    text = brain.timeline(roles=("assistant",), conversation_id=NOTES_CONVERSATION)["items"][0]["text"]
    assert "neverstorethis" not in text and "confidential" not in text


def test_connection_pool_reuses_connections_without_leaking_principals_or_failed_transactions():
    try:
        settings = Settings.from_env()
    except ValueError:
        pytest.skip("MEMORY_DATABASE_URL must point to the dedicated test database")
    database = Database(settings.database_url, pool=True)
    first, second = Principal(uid(), uid()), Principal(uid(), uid())
    try:
        with database.transaction(first) as conn:
            backend = conn.info.backend_pid
            assert conn.execute("SELECT current_setting('app.principal_id') AS value").fetchone()["value"] == first.principal_id
        with pytest.raises(RuntimeError):
            with database.transaction(first) as conn:
                conn.execute("SELECT set_config('app.principal_id','untrusted',true)")
                raise RuntimeError("rollback")
        with database.transaction(second) as conn:
            assert conn.info.backend_pid == backend
            assert conn.execute("SELECT current_setting('app.principal_id') AS value").fetchone()["value"] == second.principal_id
            assert conn.execute("SELECT current_setting('app.tenant_id') AS value").fetchone()["value"] == second.tenant_id
    finally:
        database.close()


def test_note_http_api_validates_fields_and_preserves_a_single_write_on_retry(brain):
    from fastapi.testclient import TestClient

    from membrain.api import create_app

    settings = Settings(database_url=brain.service.database.dsn, api_keys={hashlib.sha256(b"local-test").hexdigest(): brain.principal})
    with TestClient(create_app(settings, service=brain.service)) as client:
        url = f"/v1/brains/{brain.brain}/notes"
        body = {"request_id": uid(), "text": "Appointment on November 4.", "kind": "plan", "occurs_at": "2026-11-04"}
        assert client.post(url, json=body).status_code == 401
        headers = {"Authorization": "Bearer local-test"}
        first = client.post(url, json=body, headers=headers)
        assert first.status_code == 200
        assert client.post(url, json=body, headers=headers).json() == first.json()
        assert client.post(url, json={**body, "occurs_at": "2026-02-31"}, headers=headers).status_code == 422
        assert client.post(url, json={**body, "pinned": "true"}, headers=headers).status_code == 422
        assert client.get(f"/v1/brains/{brain.brain}/status", headers=headers).json()["index_lag"] == 1


def test_a_current_turn_reference_links_when_its_message_is_recorded_in_the_same_brain(brain):
    key = uid()
    ref = {"producer_ref": "timeline-test", "source_event_key": key, "source_revision": 1}
    note = write(brain, "Passport appointment is scheduled.", source_messages=[ref])
    assert note["pending_source_count"] == 1
    foreign = Brain(brain.service, brain.principal)
    foreign.say("Other brain, same external key.", source_key=key)
    current = brain.timeline(roles=("assistant",), conversation_id=NOTES_CONVERSATION)["items"][0]
    assert current["pending_source_count"] == 1 and current["source_event_ids"] == []
    source = brain.say("My passport appointment is scheduled.", source_key=key)
    current = brain.timeline(roles=("assistant",), conversation_id=NOTES_CONVERSATION)["items"][0]
    assert current["source_event_ids"] == [source] and current["pending_source_count"] == 0


def test_stale_replacement_does_not_create_an_extra_note(brain):
    first = write(brain, 'Appointment on November 4.')
    newer = write(brain, 'Appointment on November 5.', replaces=[first['note_id']])
    with pytest.raises(DomainError, match='Refresh before correcting'):
        write(brain, 'Stale edit from another tab.', replaces=[first['note_id']])
    assert [note['event_id'] for note in brain.timeline(roles=('assistant',), conversation_id=NOTES_CONVERSATION)['items']] == [newer['note_id']]


@pytest.mark.parametrize('fields', [{'about': '<private>confidential name</private>'},
                                   {'aliases': ['password=neverstorethis']}])
def test_note_name_fields_cannot_bypass_privacy_filtering(brain, fields):
    with pytest.raises(DomainError):
        write(brain, 'A lasting fact.', kind='person', **fields)
    assert brain.timeline(roles=('assistant',), conversation_id=NOTES_CONVERSATION)['items'] == []


def test_note_provenance_survives_archive_restore_without_reviving_erased_content(brain):
    import json

    import httpx

    from membrain.client import MemoryActionError, MemoryClient

    def handle(request):
        return httpx.Response(200, json=brain.service.handle(brain.principal, json.loads(request.content)))

    source = brain.say('My appointment is on November 4.')
    note = write(brain, 'Appointment on November 4.', source_event_ids=[source], kind='plan', status='scheduled')
    with MemoryClient('http://memory.test', 'test-key', brain.brain, transport=httpx.MockTransport(handle)) as client:
        archive = client.export()['result']
        first = brain.service.create_brain(brain.principal, 'Source-linked archive')['id']
        client.restore(archive['archive_id'], expected_manifest_sha256=archive['manifest_sha256'],
                       expected_scope_generation=1, mode='new_brain', brain_id=first)
        restored = brain.service.timeline(brain.principal, first, roles=('assistant',),
                                           conversation_id=NOTES_CONVERSATION)['items']
        assert len(restored) == 1 and restored[0]['event_id'] == note['note_id']
        assert restored[0]['source_event_ids'] == [source]
        generation = brain.service.dashboard(brain.principal, brain.brain)['brain']['scope_generation']
        client.forget([source], kind='events', mode='erase_content', expected_scope_generation=generation)
        second = brain.service.create_brain(brain.principal, 'Archive after erasure')['id']
        with pytest.raises(MemoryActionError, match='invalid_archive'):
            client.restore(archive['archive_id'], expected_manifest_sha256=archive['manifest_sha256'],
                           expected_scope_generation=1, mode='new_brain', brain_id=second)
        assert brain.service.timeline(brain.principal, second, roles=('user', 'assistant'))['items'] == []


def test_connection_counts_exclude_notes_and_other_brains_and_remove_erased_messages(brain):
    write(brain, "Prefers concise replies.", kind="preference", role="user")
    assert brain.service.memory_status(brain.principal, brain.brain)["recorded_messages"] == 0
    source = brain.say("My agent received this message.")
    foreign = Brain(brain.service, Principal(uid(), uid()))
    foreign.say("Another user's message.")
    status = brain.service.memory_status(brain.principal, brain.brain)
    assert status["recorded_messages"] == 1 and status["last_message_producer"] == "timeline-test"
    assert status["last_message_at"] and "agent received" not in str(status)
    generation = brain.service.dashboard(brain.principal, brain.brain)["brain"]["scope_generation"]
    brain.action("forget", {"idempotency_key": uid(), "selector": {"kind": "events", "ids": [source]},
                            "mode": "erase_content", "expected_scope_generation": generation})
    status = brain.service.memory_status(brain.principal, brain.brain)
    assert status["recorded_messages"] == 0 and status["last_message_producer"] is None
