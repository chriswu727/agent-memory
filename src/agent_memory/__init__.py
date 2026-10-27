"""Compatibility alias for code and configurations written before the memBrain rename.

``import agent_memory.<module>`` resolves to the same module object as ``membrain.<module>``.
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys

_PREFIX = "agent_memory."
_RUNNABLE = {"agent_memory.cli"}  # Real stub modules so ``python -m agent_memory.cli`` keeps working.


class _Alias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path=None, target=None):
        if name.startswith(_PREFIX) and name not in _RUNNABLE:
            return importlib.util.spec_from_loader(name, self)
        return None

    def __init__(self):
        self._specs = {}

    def create_module(self, spec):
        module = importlib.import_module("membrain." + spec.name[len(_PREFIX):])
        self._specs[module.__name__] = module.__spec__
        return module

    def exec_module(self, module):
        # The import system assigns the alias spec to the returned module; restore the real one so the
        # membrain module's relative imports and identity are unchanged.
        module.__spec__ = self._specs.pop(module.__name__, module.__spec__)


if not any(isinstance(finder, _Alias) for finder in sys.meta_path):
    sys.meta_path.insert(0, _Alias())

from membrain import *  # noqa: E402,F401,F403
from membrain import __version__  # noqa: E402,F401
