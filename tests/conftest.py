"""conftest.py — set up fake tool registry before any test module imports."""

import sys
import types

# ── Fake tool registry ───────────────────────────────────────────────────────────

_FAKE_TOOLS = {}


def _fake_echo(**kwargs):
    """Fake echo: returns the message or command arg as 'echoed'."""
    return {"echoed": kwargs.get("message") or kwargs.get("command") or str(kwargs)}


_FAKE_TOOLS["echo"] = _fake_echo
_FAKE_TOOLS["ls"] = lambda **kw: {"output": "fake ls output"}
_FAKE_TOOLS["cat"] = lambda **kw: {"output": "fake cat output"}


def _fake_get_tool(name):
    return _FAKE_TOOLS.get(name)


# Install BEFORE any test module imports workflow.executor
_tools_mod = types.ModuleType("tools")
_registry_mod = types.ModuleType("tools.registry")
_registry_mod.get_tool = _fake_get_tool
_tools_mod.registry = _registry_mod
sys.modules["tools"] = _tools_mod
sys.modules["tools.registry"] = _registry_mod
