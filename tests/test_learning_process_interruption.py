import json
import os
import select
import subprocess
import sys

import pytest
from test_learning import learning as learning
from test_learning_settlement_recovery import expire, operations
from test_learning_windows import observations

from agent_memory.jobs import JobStore
from agent_memory.storage import Database

pytestmark = pytest.mark.integration

CHILD = """
import json,sys
from agent_memory.helpers import Principal
from agent_memory.jobs import JobStore
from agent_memory.learning import LearningEngine
from agent_memory.semantic import SemanticProcessor
from agent_memory.service import MemoryService
from agent_memory.storage import Database
config=json.loads(sys.stdin.readline())
principal=Principal(config['tenant'],config['owner'])
database=Database(config['dsn'])
job=JobStore(database).claim(principal,config['brain'],worker_id='interrupted-process',kinds=['consolidate'])
assert job is not None
def provider(request):
    print(json.dumps({'job_id':job['id'],'phase':request.phase}),flush=True)
    sys.stdin.read()
    raise RuntimeError('Parent must terminate this process before a response is returned')
service=MemoryService(database,semantic_processor=SemanticProcessor(provider,provider))
LearningEngine(service)._execute(principal,config['brain'],job)
"""


def test_killed_process_leaves_one_charged_unknown_operation_and_cannot_replay_it(learning):
    observations(learning, 1)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("DEEPSEEK", "OPENAI", "ANTHROPIC", "MEMORY_COGNITION", "MEMORY_EMBEDDING"))}
    environment["PYTHON_DOTENV_DISABLED"] = "1"
    process = subprocess.Popen([sys.executable, "-u", "-c", CHILD], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
    try:
        process.stdin.write(json.dumps({"tenant": learning.principal.tenant_id,
                                       "owner": learning.principal.principal_id,
                                       "brain": learning.brain, "dsn": learning.database.dsn}) + "\n")
        process.stdin.flush()
        assert select.select([process.stdout], [], [], 15)[0], "Child did not reach its reserved provider call"
        entered = json.loads(process.stdout.readline())
        assert entered["phase"] == "proposal"
        process.kill()
        assert process.wait(timeout=10) != 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()
    identifier = entered["job_id"]
    before = learning.job(identifier)
    assert before["state"] == "running" and before["model_calls"] == 1
    assert [row["outcome"] for row in operations(learning, identifier)] == ["reserved"]
    expire(learning, identifier)
    fresh = JobStore(Database(learning.database.dsn))
    assert fresh.claim(learning.principal, learning.brain, worker_id="restarted-process", job_id=identifier) is None
    stopped = learning.job(identifier)
    assert stopped["state"] == "cancelled" and stopped["last_error"] == "provider_uncertain"
    assert stopped["model_calls"] == before["model_calls"] == 1
    assert [row["outcome"] for row in operations(learning, identifier)] == ["uncertain"]
    assert fresh.claim(learning.principal, learning.brain, worker_id="another-process", job_id=identifier) is None
    assert learning.calls == []
    usage = fresh.cognitive_usage(learning.principal, learning.brain)
    assert usage["reservation_count"] == 1 and usage["outcome_counts"]["uncertain"] == 1
