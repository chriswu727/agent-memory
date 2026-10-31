"""Run the lexical-only review fixture in a new, isolated installation (no model calls)."""

import argparse
import atexit
import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from membrain import desktop, host
from membrain.local_profile import ensure_profile

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["MEMBRAIN_SEMANTIC"] = "0"

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--directory", type=Path, required=True)
parser.add_argument("--database-port", type=int, required=True)
parser.add_argument("--api-port", type=int, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
root = args.directory
if root.exists():
    parser.error("--directory must be a new, dedicated benchmark directory")
ensure_profile(
    root, database_engine="embedded", database_port=args.database_port, api_port=args.api_port
)
atexit.register(lambda: desktop.stop(root))
host.start(root)
user = "review-performance-only"
rows = []


def stats(samples):
    values = sorted(samples)
    return {
        "n": len(values),
        "median_ms": round(statistics.median(values) * 1000, 2),
        "p95_ms": round(values[int(0.95 * (len(values) - 1))] * 1000, 2),
        "max_ms": round(max(values) * 1000, 2),
    }


def query(i):
    with host.ready_client(root, user) as client:
        start = time.perf_counter()
        context = host.recall_context(
            client,
            f"What is the appointment for project{i % 50:04d}?",
            current_conversation="benchmark-query",
        )
        return time.perf_counter() - start, bool(context)


with host.ready_client(root, user) as client:
    total = 0
    for target in (100, 1000):
        started = time.perf_counter()
        for i in range(total, target):
            client.observe(
                f"For project{i % 50:04d}, my appointment {i} is at the Vancouver library on Friday. Bring the blue notebook and arrive at ten.",
                conversation_id=f"history-{i // 10}",
                branch_id="main",
                source_event_key=f"perf-{i}",
                role="user",
            )
        ingest = time.perf_counter() - started
        deadline = time.monotonic() + 180
        while True:
            status = client.dashboard()["status"]
            if not status.get("pending_jobs") and not status.get("index_lag"):
                break
            if time.monotonic() > deadline:
                raise RuntimeError(f"Index backlog: {status}")
            time.sleep(1)
        settled = time.perf_counter() - started
        serial = [query(i) for i in range(20)]
        row = {
            "events": target,
            "added": target - total,
            "ingest_seconds": round(ingest, 2),
            "ingest_and_index_seconds": round(settled, 2),
            "sequential_recall": stats([x[0] for x in serial]),
            "nonempty_results": sum(x[1] for x in serial),
        }
        if target == 1000:
            with ThreadPoolExecutor(max_workers=8) as executor:
                concurrent = list(executor.map(query, range(24)))
            row["eight_concurrent_recall"] = stats([x[0] for x in concurrent])
        rows.append(row)
        print(json.dumps(row), flush=True)
        total = target
args.output.write_text(
    json.dumps(
        {
            "mode": "lexical only; synthetic fixture; warm process; excludes ready_client startup; not model generation latency",
            "results": rows,
        },
        indent=2,
    )
)
