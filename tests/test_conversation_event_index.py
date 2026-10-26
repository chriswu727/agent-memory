import json

import psycopg
import pytest

from agent_memory.build_identity import MIGRATIONS
from agent_memory.config import Settings

pytestmark = pytest.mark.integration

NEIGHBORS = """
SELECT seed.id AS seed_id, near.* FROM memory_events seed CROSS JOIN LATERAL (
    (SELECT e.* FROM memory_events e
     WHERE e.tenant_id=seed.tenant_id AND e.owner_id=seed.owner_id
       AND e.brain_id=seed.brain_id AND e.conversation_id=seed.conversation_id
       AND COALESCE('value:' || e.branch_id,'null')=COALESCE('value:' || seed.branch_id,'null')
       AND e.is_current
       AND e.sequence<seed.sequence ORDER BY e.sequence DESC LIMIT 1)
    UNION ALL
    (SELECT e.* FROM memory_events e
     WHERE e.tenant_id=seed.tenant_id AND e.owner_id=seed.owner_id
       AND e.brain_id=seed.brain_id AND e.conversation_id=seed.conversation_id
       AND COALESCE('value:' || e.branch_id,'null')=COALESCE('value:' || seed.branch_id,'null')
       AND e.is_current
       AND e.sequence>seed.sequence ORDER BY e.sequence LIMIT 1)
) near
WHERE seed.tenant_id='tenant' AND seed.owner_id='owner' AND seed.brain_id='brain'
  AND seed.is_current AND seed.id='seed' AND seed.conversation_id IS NOT NULL
ORDER BY seed.sequence, near.sequence LIMIT 32
"""


@pytest.fixture(params=[None, "main", ""])
def history(request):
    settings = Settings.from_env()
    with psycopg.connect(settings.migration_database_url or settings.database_url) as conn:
        conn.execute("CREATE TEMP TABLE memory_events (tenant_id text, owner_id text, brain_id text, "
                     "id text PRIMARY KEY, conversation_id text, branch_id text, sequence bigint, "
                     "is_current boolean, deleted boolean, suppressed boolean, retired boolean)")
        conn.execute("CREATE INDEX memory_events_tail ON memory_events (tenant_id,brain_id,sequence)")
        conn.execute("INSERT INTO memory_events SELECT 'tenant','owner','brain',n::text,"
                     "'unrelated-' || (n % 100)::text,'main',2*n-1,true,false,false,false "
                     "FROM generate_series(1,20000) n")
        conn.execute("INSERT INTO memory_events SELECT 'tenant','owner','brain','branch-' || n::text,"
                     "'target','fork',2*n,true,false,false,false FROM generate_series(1,20000) n")
        conn.execute("INSERT INTO memory_events VALUES "
                     "('tenant','owner','brain','before','target',%s,100,true,true,false,false),"
                     "('tenant','owner','brain','seed','target',%s,20000,true,false,false,false),"
                     "('tenant','owner','brain','after','target',%s,39900,true,false,true,true),"
                     "('tenant','owner','brain','old','target',NULL,19998,false,false,false,false),"
                     "('tenant','owner','brain','branch','target','fork',19999,true,false,false,false),"
                     "('tenant','other','brain','owner','target',NULL,20001,true,false,false,false),"
                     "('other','owner','brain','tenant','target',NULL,20002,true,false,false,false),"
                     "('tenant','owner','other','brain','target',NULL,20003,true,false,false,false)",
                     (request.param, request.param, request.param))
        conn.execute("INSERT INTO memory_events VALUES "
                     "('tenant','owner','brain','null-or-empty','target',%s,19997,true,false,false,false)",
                     ("" if request.param is None else None,))
        conn.execute("ANALYZE memory_events")
        yield conn
        conn.rollback()


def plan_nodes(node):
    yield node
    for child in node.get("Plans", []):
        yield from plan_nodes(child)


def filtered_rows(plan):
    return sum(node.get("Rows Removed by Filter", 0) * node.get("Actual Loops", 1)
               for node in plan_nodes(plan["Plan"]))


def test_index_keeps_exact_neighbors_and_hidden_barriers_while_avoiding_unrelated_scans(history):
    before = history.execute(NEIGHBORS).fetchall()
    old_plan = history.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + NEIGHBORS).fetchone()[0][0]
    migration = dict(MIGRATIONS)["023_conversation_event_index.sql"]
    history.execute(migration.decode(), prepare=False)
    after = history.execute(NEIGHBORS).fetchall()
    new_plan = history.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + NEIGHBORS).fetchone()[0][0]
    assert after == before
    assert [row[4] for row in after] == ["before", "after"]
    assert after[0][-3:] == (True, False, False)
    assert after[1][-3:] == (False, True, True)
    assert "memory_events_conversation_tail" in json.dumps(new_plan)
    assert filtered_rows(old_plan) > 10000
    assert filtered_rows(new_plan) < 10
