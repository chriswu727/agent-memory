import re
from pathlib import Path


def test_packaged_host_launchers_pin_the_same_immutable_runtime_revision():
    root = Path(__file__).parents[1]
    package = root / 'plugins' / 'membrain'
    files = [package / name for name in ('.claude-mcp.json', '.codex-mcp.json', '.codebuddy-mcp.json',
             'hooks/claude-hooks.json', 'hooks/codex-hooks.json', 'bin/membrain-hook', 'bin/membrain-hook.cmd')]
    files.append(root / 'integrations' / 'openclaw' / 'membrain' / 'index.js')
    revisions = set()
    for path in files:
        matches = re.findall(r'git\+https://github\.com/chriswu727/membrain(?:@([0-9a-f]+))?(?![\w@./-])',
                             path.read_text(), re.IGNORECASE)
        assert matches and all(len(revision) == 40 for revision in matches), path
        revisions.update(matches)
    assert len(revisions) == 1
