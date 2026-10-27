"""Blinded sheet for one scenario variant across arms; mapping sealed in a separate file.

Usage: python blind3.py scenario_b short LSFN
"""
import importlib
import json
import secrets
import sys
from pathlib import Path

name, variant, arms_text = sys.argv[1], sys.argv[2], sys.argv[3]
CASES = importlib.import_module(name).CASES
HERE = Path(__file__).resolve().parent
scenario = f"{name}_{variant}"
labels = "ABCD"[:len(arms_text)]
rows = {}
for arm in arms_text:
    for line in (HERE / f"results_{scenario}_{arm}.jsonl").read_text().splitlines():
        row = json.loads(line)
        rows[(arm, row["session"], row["turn"])] = row
sheet, mapping = [], {}
for session, turn, case_id, criteria in CASES:
    arms = list(arms_text)
    secrets.SystemRandom().shuffle(arms)
    mapping[case_id] = dict(zip(labels, arms))
    sheet.append(f"## {case_id}\n\n**Criteria:** {criteria}\n\n**User:** {rows[(arms[0], session, turn)]['input']}\n")
    for label, arm in zip(labels, arms):
        sheet.append(f"### {label}\n\n{rows[(arm, session, turn)]['reply'] or '(no reply)'}\n")
(HERE / f"blind_{scenario}.md").write_text("\n".join(sheet))
(HERE / f"sealed_{scenario}.json").write_text(json.dumps(mapping, indent=1))
print(scenario, "cases:", len(CASES))
