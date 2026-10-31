from .helpers import digest
from .notes import render_coming_up, render_notes


def _notes(saved, budget, **options):
    selection_budget = budget
    for _ in range(4):
        block = render_notes(saved, budget=selection_budget, **options)
        if not block or len(block) <= budget:
            return block
        selection_budget = max(0, selection_budget - (len(block) - budget) - 100)
    return "<membrain-notes>Saved notes did not fit. Search memory when personal context matters.</membrain-notes>"


def snapshot(saved, *, brain_id, tools, skip_imports_from=(), shared=None):
    blocks = [render_coming_up(saved, skip_imports_from=skip_imports_from, budget=1000),
              _notes(saved, 2800 if shared is not None else 3600, tool=tools["note"] if tools else None,
                     skip_imports_from=skip_imports_from)]
    if shared is not None:
        blocks.append(_notes(shared, 1100, tool=None, real_life=True))
    text = "\n\n".join(block for block in blocks if block)
    return {"fingerprint": digest([brain_id, text]), "text": text}


def update(current, previous):
    if current["fingerprint"] == previous:
        return None
    return ("<membrain-updates>\nSaved memory has been refreshed. This snapshot replaces earlier saved-note "
            "and coming-up blocks in this session. Do not use earlier versions as current notes; an omitted "
            "note may have been removed or may not fit. Omission does not prove a fact false. Search when "
            "personal context is missing. Original conversations remain historical data.\n</membrain-updates>"
            + ("\n\n" + current["text"] if current["text"] else "\nNo saved notes are currently shown."))
