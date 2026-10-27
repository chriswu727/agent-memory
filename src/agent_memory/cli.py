"""Compatibility entry point for ``python -m agent_memory.cli`` in configurations written before the rename."""
from membrain.cli import main

if __name__ == "__main__":
    main()
