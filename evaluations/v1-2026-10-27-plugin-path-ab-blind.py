"""Blinded sheet for one scenario across arms P, F, N; mapping sealed in a separate file."""
import json
import secrets
import sys
from pathlib import Path

from scenario import CASES

scenario = sys.argv[1]
ARMS = sys.argv[2] if len(sys.argv) > 2 else "PFN"
LABELS = "ABCD"[:len(ARMS)]
HERE = Path(__file__).resolve().parent
rows = {}
for arm in ARMS:
    for line in (HERE / f"results_{scenario}_{arm}.jsonl").read_text().splitlines():
        row = json.loads(line)
        rows[(arm, row["session"], row["turn"])] = row
sheet, mapping = [], {}
for session, turn, case_id, criteria in CASES:
    arms = list(ARMS)
    secrets.SystemRandom().shuffle(arms)
    mapping[case_id] = dict(zip(LABELS, arms))
    sheet.append(f"## {case_id}\n\n**Criteria:** {criteria}\n\n**User:** {rows[(arms[0], session, turn)]['input']}\n")
    for label, arm in zip(LABELS, arms):
        sheet.append(f"### {label}\n\n{rows[(arm, session, turn)]['reply'] or '(no reply)'}\n")
(HERE / f"blind_{scenario}.md").write_text("\n".join(sheet))
(HERE / f"sealed_{scenario}.json").write_text(json.dumps(mapping, indent=1))
print(scenario, "cases:", len(CASES))
