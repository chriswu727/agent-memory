"""Pair LongMemEval arms on the questions memBrain answered; print tables for the report.

Usage: python pair_arms.py RUN_DIR, where RUN_DIR holds shard-*/answers-M.jsonl and nf/answers-{F,N}.jsonl.
"""
import collections
import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
m = {r["id"]: r for path in sorted(run.glob("shard-*/answers-M.jsonl")) for r in map(json.loads, path.open())}
f = {r["id"]: r for r in map(json.loads, (run / "nf/answers-F.jsonl").open())}
n = {r["id"]: r for r in map(json.loads, (run / "nf/answers-N.jsonl").open())}
ids = sorted(m)
print("paired questions", len(ids))
types = ["single-session-user", "single-session-assistant", "single-session-preference", "multi-session",
         "knowledge-update", "temporal-reasoning"]
def group(i):
    return "abstention" if m[i]["abstention"] else m[i]["type"]
for name, arm in (("M", m), ("F", f), ("N", n)):
    cells = []
    for kind in types + ["abstention"]:
        sel = [i for i in ids if group(i) == kind]
        cells.append(f"{sum(arm[i]['correct'] for i in sel)}/{len(sel)}" if sel else "-")
    print(name, " | ".join(cells), "| overall", f"{sum(arm[i]['correct'] for i in ids)}/{len(ids)}",
          f"{100 * sum(arm[i]['correct'] for i in ids) / len(ids):.1f}%")
both = collections.Counter((m[i]["correct"], f[i]["correct"]) for i in ids)
print("M right F wrong", both[(True, False)], "| M wrong F right", both[(False, True)], "| both right", both[(True, True)],
      "| both wrong", both[(False, False)])
print("F on all 62:", f"{100 * sum(r['correct'] for r in f.values()) / len(f):.1f}%", "| F on paired:",
      f"{100 * sum(f[i]['correct'] for i in ids) / len(ids):.1f}%")
ctx = [m[i].get("context_characters", 0) for i in ids]
print("M context chars mean", sum(ctx) // len(ctx), "| searches used on", sum(1 for i in ids if m[i].get("searches")))
print("M seconds mean", sum(m[i]["seconds"] for i in ids) / len(ids))
for i in ids:
    if not m[i]["correct"]:
        print("MISS", i, group(i), "F:", f[i]["correct"], "|", m[i]["question"][:90], "| gold:", str(m[i]["gold"])[:60],
              "| got:", m[i]["answer"][:100].replace("\n", " "))
