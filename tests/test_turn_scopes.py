from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row
from test_runtime import memory as memory
from test_turns import turns as turns

from membrain.config import Settings
from membrain.helpers import DomainError, Principal, digest, uid
from membrain.runtime import BrainRuntime, DeliveryReceipt, RuntimeBlocked
from membrain.runtime_client import RuntimeClient
from membrain.storage import Database
from membrain.turns import TurnStore

pytestmark = pytest.mark.integration
MIGRATION = Path(__file__).parents[1] / "migrations" / "012_runtime_scope_reservations.sql"


def begin(turns, turn_id="second", **changes):
    return turns.store.begin(turns.principal, turns.brain, turn_id=turn_id,
                             conversation_id=changes.get("conversation_id", turns.conversation),
                             branch_id=changes.get("branch_id", turns.branch), input_fingerprint=digest(turn_id))


def assert_blocked(turns, turn_id="second"):
    with pytest.raises(DomainError) as error:
        begin(turns, turn_id)
    assert error.value.code == "scope_conflict"


def insert_legacy(turns, turn_id, *, age=0, state="started"):
    with turns.database.transaction(turns.principal) as conn:
        conn.execute(
            "INSERT INTO memory_runtime_turns(tenant_id,owner_id,brain_id,turn_id,conversation_id,branch_id,"
            "input_fingerprint,stage,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,"
            "clock_timestamp()-make_interval(secs=>%s))",
            (turns.principal.tenant_id, turns.principal.principal_id, turns.brain, turn_id,
             turns.conversation, turns.branch, digest(turn_id), state, age),
        )


def test_new_turn_reserves_scope_and_exact_begin_replays_without_mutation(turns):
    first = turns.begin()
    assert first["scope_reserved"] is True
    assert turns.begin() == first
    assert_blocked(turns)
    assert len(turns.store.list(turns.principal, turns.brain)) == 1
    assert len(turns.call("history")) == 1
    with pytest.raises(DomainError) as changed:
        turns.begin(input_fingerprint=digest("changed"))
    assert changed.value.code == "idempotency_conflict"


@pytest.mark.parametrize("same_id", [False, True])
def test_concurrent_hosts_cannot_create_two_scope_owners(turns, same_id):
    barrier = Barrier(2)

    def start(index):
        barrier.wait()
        try:
            return begin(turns, "one" if same_id else f"turn-{index}")
        except DomainError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(start, range(2)))
    accepted = [result for result in results if isinstance(result, dict)]
    assert len(accepted) == (2 if same_id else 1)
    assert all(result["scope_reserved"] for result in accepted)
    assert len(turns.store.list(turns.principal, turns.brain)) == 1
    if same_id:
        assert accepted[0] == accepted[1]
    else:
        assert "scope_conflict" in results


@pytest.mark.parametrize("scope", [{"branch_id": "alternative"}, {"conversation_id": "other"}])
def test_independent_conversation_branches_can_be_reserved_together(turns, scope):
    turns.begin()
    assert begin(turns, **scope)["scope_reserved"] is True
    assert len(turns.store.list(turns.principal, turns.brain)) == 2


def test_different_brains_and_owners_do_not_share_reservations(turns):
    turns.begin()
    principal = Principal(turns.principal.tenant_id, uid())
    brain = turns.service.create_brain(principal, "Separate owner")['id']
    row = turns.store.begin(principal, brain, turn_id=turns.turn_id, conversation_id=turns.conversation,
                            branch_id=turns.branch, input_fingerprint=digest("other"))
    assert row["scope_reserved"] is True
    with pytest.raises(DomainError) as isolated:
        turns.store.begin(principal, turns.brain, turn_id="private-probe", conversation_id=turns.conversation,
                          branch_id=turns.branch, input_fingerprint=digest("other"))
    assert isolated.value.code == "not_found_or_forbidden"


def test_release_and_worker_expiry_preserve_lifetime_scope_ownership(turns):
    turns.begin()
    first = turns.claim()
    released = turns.call("release", lease_token=turns.token, expected_revision=first["revision"])
    assert released["scope_reserved"] is True and released["lease_active"] is False
    assert_blocked(turns)
    second = turns.claim()
    turns.expire()
    assert_blocked(turns)
    recovered = TurnStore(turns.database).claim(turns.principal, turns.brain, turns.turn_id,
                                               worker_id=uid(), expected_revision=second["revision"])
    assert recovered["scope_reserved"] and recovered["fence"] == second["fence"] + 1


def test_unknown_generation_keeps_scope_reserved_until_explicit_failure(turns):
    turns.prepare()
    turns.advance("generating")
    turns.expire()
    assert_blocked(turns)
    claimed = turns.claim()
    assert claimed["recovery_required"] == "generation_uncertain"
    failed = turns.call("fail", lease_token=turns.token, expected_revision=claimed["revision"],
                        failure_code="generation_uncertain")
    assert failed["stage"] == "failed" and failed["scope_reserved"] is False
    assert begin(turns)["scope_reserved"] is True
    assert turns.begin()["stage"] == "failed"


@pytest.mark.parametrize("cancelled", [False, True])
def test_completion_and_cancelled_output_release_scope_atomically(turns, cancelled):
    turns.prepare()
    turns.advance("generating")
    turns.record_output("A bounded reply.")
    turns.advance("delivering")
    final = turns.record_output("" if cancelled else "A bounded reply.",
                                "cancelled" if cancelled else "delivered")
    if not cancelled:
        assert final["scope_reserved"] is True
        assert_blocked(turns)
        final = turns.call("finish", lease_token=turns.token, expected_revision=final["revision"])
    assert final["stage"] == ("failed" if cancelled else "completed")
    assert final["scope_reserved"] is False
    assert begin(turns)["scope_reserved"] is True


def test_deleted_sources_do_not_release_an_unknown_turn_or_resurrect_content(turns):
    turns.prepare()
    turns.advance("generating")
    turns.action("forget", {"idempotency_key": uid(),
                            "selector": {"kind": "events", "ids": [turns.row["input_event_refs"][0]["event_id"]]},
                            "mode": "erase_content", "expected_scope_generation": 1})
    row = turns.call("get")
    assert row["scope_reserved"] is True and row["sources_available"] is False
    assert_blocked(turns)
    turns.call("fail", lease_token=turns.token, expected_revision=row["revision"], failure_code="source_unavailable")
    assert begin(turns)["scope_reserved"] is True


def test_legacy_queue_requires_oldest_turn_before_newer_recovery_or_new_input(turns):
    insert_legacy(turns, "old", age=120)
    insert_legacy(turns, "newer", age=60)
    assert_blocked(turns)
    with pytest.raises(DomainError) as queued:
        turns.store.claim(turns.principal, turns.brain, "newer", worker_id=uid())
    assert queued.value.code == "scope_conflict"
    oldest = turns.store.claim(turns.principal, turns.brain, "old", worker_id=uid())
    assert oldest["scope_reserved"] is True
    assert begin(turns, "newer")["scope_reserved"] is False
    turns.store.fail(turns.principal, turns.brain, "old", lease_token=oldest["lease_token"],
                     expected_revision=oldest["revision"], failure_code="cancelled")
    assert_blocked(turns)
    recovered = turns.store.claim(turns.principal, turns.brain, "newer", worker_id=uid())
    assert recovered["scope_reserved"] is True
    assert len(turns.store.list(turns.principal, turns.brain)) == 2


def test_scope_lookup_finds_old_owner_beyond_recent_turn_listing(turns):
    first = turns.begin()
    with turns.database.transaction(turns.principal) as conn:
        conn.execute("UPDATE memory_runtime_turns SET created_at=clock_timestamp()-interval '1 day',"
                     "updated_at=clock_timestamp()-interval '1 day' WHERE tenant_id=%s AND brain_id=%s AND turn_id=%s",
                     (turns.principal.tenant_id, turns.brain, turns.turn_id))
        conn.cursor().executemany(
            "INSERT INTO memory_runtime_turns(tenant_id,owner_id,brain_id,turn_id,conversation_id,branch_id,"
            "input_fingerprint,stage) VALUES(%s,%s,%s,%s,%s,%s,%s,'completed')",
            [(turns.principal.tenant_id, turns.principal.principal_id, turns.brain, f"historical-{index}",
              turns.conversation, turns.branch, digest(index)) for index in range(205)],
        )
    assert turns.turn_id not in {row["turn_id"] for row in turns.store.list(turns.principal, turns.brain, limit=200)}
    active = turns.store.active_scope(turns.principal, turns.brain,
                                     conversation_id=turns.conversation, branch_id=turns.branch)
    assert active["turn_id"] == first["turn_id"] and active["scope_reserved"] is True
    assert active == turns.call("get")


def test_scope_lookup_is_isolated_and_validates_identifiers(turns):
    turns.begin()
    assert turns.store.active_scope(turns.principal, turns.brain,
                                    conversation_id=turns.conversation, branch_id="other") is None
    assert turns.store.active_scope(turns.principal, turns.brain,
                                    conversation_id="other", branch_id=turns.branch) is None
    with pytest.raises(DomainError) as outsider:
        turns.store.active_scope(Principal(turns.principal.tenant_id, uid()), turns.brain,
                                  conversation_id=turns.conversation, branch_id=turns.branch)
    assert outsider.value.code == "not_found_or_forbidden"
    with pytest.raises(DomainError) as invalid:
        turns.store.active_scope(turns.principal, turns.brain,
                                  conversation_id="contains whitespace", branch_id=turns.branch)
    assert invalid.value.code == "validation_failed"


def test_scope_lookup_prioritizes_reserved_owner_then_oldest_legacy_without_mutation(turns):
    current = turns.begin()
    insert_legacy(turns, "older-legacy", age=120)
    insert_legacy(turns, "later-legacy", age=60)
    arguments = {"conversation_id": turns.conversation, "branch_id": turns.branch}
    assert turns.store.active_scope(turns.principal, turns.brain, **arguments)["turn_id"] == current["turn_id"]
    with turns.database.transaction(turns.principal) as conn:
        conn.execute("UPDATE memory_runtime_turns SET scope_reserved=false,stage='failed' "
                     "WHERE tenant_id=%s AND brain_id=%s AND turn_id=%s",
                     (turns.principal.tenant_id, turns.brain, turns.turn_id))
    before = turns.store.list(turns.principal, turns.brain)
    legacy = turns.store.active_scope(turns.principal, turns.brain, **arguments)
    assert legacy["turn_id"] == "older-legacy" and legacy["scope_reserved"] is False
    assert turns.store.list(turns.principal, turns.brain) == before


def test_legacy_nonowner_worker_is_fenced_even_with_a_live_lease(turns):
    turns.begin()
    current = turns.claim()
    with turns.database.transaction(turns.principal) as conn:
        conn.execute("UPDATE memory_runtime_turns SET scope_reserved=false WHERE tenant_id=%s AND brain_id=%s AND turn_id=%s",
                     (turns.principal.tenant_id, turns.brain, turns.turn_id))
    for method, arguments in (
        ("renew", {}),
        ("release", {"expected_revision": current["revision"]}),
        ("advance", {"expected_revision": current["revision"], "stage": "captured"}),
    ):
        with pytest.raises(DomainError) as blocked:
            turns.call(method, lease_token=turns.token, **arguments)
        assert blocked.value.code == "scope_conflict"
    assert turns.call("get")["revision"] == current["revision"]


def test_manual_completion_cannot_bypass_commit_or_release_scope(turns):
    turns.begin()
    turns.claim()
    with pytest.raises(DomainError) as blocked:
        turns.advance("completed")
    assert blocked.value.code == "validation_failed"
    assert turns.call("get")["scope_reserved"] is True
    assert_blocked(turns)


def test_remote_provider_wait_holds_scope_without_holding_database_lock(memory):
    started, continue_generation = Event(), Event()
    calls = []

    def generate(request):
        calls.append(request.request_id)
        started.set()
        assert continue_generation.wait(10)
        return "Delivered after another host checks the scope."

    runtime = BrainRuntime(memory, generate, deliver=lambda _: DeliveryReceipt("delivered"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        ongoing = pool.submit(runtime.respond, "First input", message_id="first", conversation_id="chat")
        try:
            assert started.wait(10)
            with pytest.raises(RuntimeBlocked) as blocked:
                BrainRuntime(memory, generate).respond("Second input", message_id="second", conversation_id="chat")
            assert blocked.value.code == "scope_conflict"
            independent = RuntimeClient(memory).begin(turn_id="independent", conversation_id="chat",
                                                       branch_id="other", input_fingerprint=digest("independent"))
            assert independent["scope_reserved"] is True
            assert len(calls) == 1
        finally:
            continue_generation.set()
        assert ongoing.result(timeout=15).stage == "completed"


@pytest.fixture
def legacy_database():
    settings = Settings.from_env()
    with psycopg.connect(settings.migration_database_url or settings.database_url, row_factory=dict_row) as conn:
        namespace = "scope_upgrade_" + uid().replace("-", "")
        try:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(namespace)))
            conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(namespace)))
            conn.execute("CREATE TABLE memory_runtime_turns (tenant_id text,owner_id text,brain_id text,"
                         "turn_id text,conversation_id text,branch_id text,stage text,created_at timestamptz,"
                         "lease_token_digest text,PRIMARY KEY(tenant_id,brain_id,turn_id))")
            yield conn, namespace
        finally:
            conn.rollback()


def test_migration_preserves_duplicate_legacy_rows_and_reserves_deterministic_oldest(legacy_database):
    conn, _ = legacy_database
    rows = [
        ("tenant", "owner", "brain", "done", "chat", "main", "completed", "2025-01-01", "old-done"),
        ("tenant", "owner", "brain", "b", "chat", "main", "generating", "2025-02-01", "old-b"),
        ("tenant", "owner", "brain", "a", "chat", "main", "started", "2025-02-01", "old-a"),
        ("tenant", "owner", "brain", "c", "chat", "other", "delivering", "2025-03-01", "old-c"),
        ("other", "owner", "brain", "a", "chat", "main", "captured", "2025-04-01", "other-a"),
    ]
    conn.cursor().executemany("INSERT INTO memory_runtime_turns VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
    before = conn.execute("SELECT * FROM memory_runtime_turns ORDER BY tenant_id,turn_id").fetchall()
    conn.execute(MIGRATION.read_text(), prepare=False)
    after = conn.execute("SELECT * FROM memory_runtime_turns ORDER BY tenant_id,turn_id").fetchall()
    assert [{key: value for key, value in row.items() if key != "scope_reserved"} for row in after] == before
    assert {(row["tenant_id"], row["turn_id"]) for row in after if row["scope_reserved"]} == {
        ("tenant", "a"), ("tenant", "c"), ("other", "a"),
    }
    with pytest.raises(psycopg.errors.UniqueViolation):
        with conn.transaction():
            conn.execute("UPDATE memory_runtime_turns SET scope_reserved=true WHERE tenant_id='tenant' AND turn_id='b'")


def test_migration_cannot_silently_skip_legacy_rows_hidden_by_forced_rls(legacy_database):
    conn, namespace = legacy_database
    role = "scope_owner_" + uid().replace("-", "")
    conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role)))
    conn.execute(sql.SQL("GRANT USAGE,CREATE ON SCHEMA {} TO {}").format(sql.Identifier(namespace), sql.Identifier(role)))
    conn.execute("INSERT INTO memory_runtime_turns VALUES('tenant','owner','brain','old','chat','main',"
                 "'started','2025-01-01','old-token')")
    conn.execute(sql.SQL("ALTER TABLE memory_runtime_turns OWNER TO {}").format(sql.Identifier(role)))
    conn.execute("ALTER TABLE memory_runtime_turns ENABLE ROW LEVEL SECURITY")
    conn.execute("ALTER TABLE memory_runtime_turns FORCE ROW LEVEL SECURITY")
    conn.execute("CREATE POLICY isolation ON memory_runtime_turns USING (false) WITH CHECK (false)")
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
        with conn.transaction():
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
            conn.execute(MIGRATION.read_text(), prepare=False)
    columns = conn.execute("SELECT column_name FROM information_schema.columns "
                           "WHERE table_schema=%s AND table_name='memory_runtime_turns'", (namespace,)).fetchall()
    assert "scope_reserved" not in {column["column_name"] for column in columns}
    assert conn.execute("SELECT count(*) AS count FROM memory_runtime_turns").fetchone()["count"] == 1


def test_migration_runner_does_not_reassign_existing_scope_ownership(turns):
    row = turns.begin()
    settings = Settings.from_env()
    database = Database(settings.database_url, settings.migration_database_url)
    database.migrate()
    database.migrate()
    assert turns.call("get") == row
