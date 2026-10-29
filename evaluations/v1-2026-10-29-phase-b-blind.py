"""Blinded review sheet for one Phase B scenario across arms; the label mapping is sealed in a separate file.

Usage: python v1-2026-10-29-phase-b-blind.py OUT_DIR SCENARIO_FILE PONF
Writes OUT_DIR/blind_<scenario>.md (facts, then each case with shuffled labels) and OUT_DIR/sealed_<scenario>.json.
"""
import importlib.util
import json
import secrets
import sys
from pathlib import Path

out, scenario_path, arms = Path(sys.argv[1]), sys.argv[2], list(sys.argv[3])
spec = importlib.util.spec_from_file_location("scenario", scenario_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
scenario = Path(scenario_path).stem
labels = "ABCD"[:len(arms)]
rows = {}
for arm in arms:
    for line in (out / f"results_{scenario}_{arm}.jsonl").read_text().splitlines():
        row = json.loads(line)
        rows[(arm, row["session"], row["turn"])] = row
sheet, mapping = [f"# {scenario}\n\n## Ground truth\n\n{module.FACTS.strip()}\n"], {}
for session, turn, case_id, criteria in module.CASES:
    shuffled = list(arms)
    secrets.SystemRandom().shuffle(shuffled)
    mapping[case_id] = dict(zip(labels, shuffled))
    first = rows[(shuffled[0], session, turn)]
    sheet.append(f"## {case_id}\n\n**Date:** {first['date']}\n\n**Criteria:** {criteria.removeprefix('Criteria:').strip()}\n\n**User:** {first['input']}\n")
    for label, arm in zip(labels, shuffled):
        sheet.append(f"### {label}\n\n{rows[(arm, session, turn)]['reply'] or '(no reply)'}\n")
(out / f"blind_{scenario}.md").write_text("\n".join(sheet))
(out / f"sealed_{scenario}.json").write_text(json.dumps(mapping, indent=1))
print(scenario, "cases:", len(module.CASES))
