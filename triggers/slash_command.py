"""Slash command dispatcher: /workflow <verb>."""

from __future__ import annotations

import re
from typing import Any


# ── Built-in template YAML ───────────────────────────────────────────────────


DEFAULT_TEMPLATE = """\
name: {name}
version: 1
description: ""
concurrency: sequential  # sequential | parallel | hybrid
max_duration: 0  # seconds, 0 = no limit
error_policy: fail_fast  # fail_fast | retry | degrade | manual
rollback_policy: checkpoint  # none | checkpoint | saga | version

context: {{}}
  # pr_number: ""
  # repo: ""

steps:
  - name: step1
    type: tool
    args:
      tool: echo
      message: "Hello from {name}"
    # requires: []        # list of step names this depends on
    # on_error: stop      # stop | skip | continue | rollback | degrade | retry
    # retry:
    #   max_attempts: 3
    #   backoff: exponential
"""


class WorkflowSlashDispatcher:
    """Parses /workflow <verb> ... arguments and calls appropriate tool function."""

    VERBS = {
        "run": "_handle_run",
        "define": "_handle_define",
        "list": "_handle_list",
        "show": "_handle_show",
        "delete": "_handle_delete",
        "history": "_handle_history",
        "status": "_handle_status",
        "stop": "_handle_stop",
        "rollback": "_handle_rollback",
        "export": "_handle_export",
        "import": "_handle_import",
        "suggest": "_handle_suggest",
    }

    def __init__(self, tools_module):
        self._tools = tools_module

    def dispatch(self, raw: str) -> dict[str, Any]:
        """Parse and dispatch a /workflow <verb> command. Returns tool result dict."""
        # Tokenize: /workflow run my-ci pr=123 repo=foo
        tokens = raw.strip().split()
        if len(tokens) < 2:
            return {"ok": False, "error": "Usage: /workflow <verb> [args]"}
        verb = tokens[1]
        handler = self.VERBS.get(verb)
        if not handler:
            return {"ok": False, "error": f"Unknown verb '{verb}'. Available: {', '.join(self.VERBS.keys)}"}
        return getattr(self, handler)(tokens[2:])

    # ── Handlers ─────────────────────────────────────────────────────────────────

    def _parse_key_val(self, args: list[str]) -> dict[str, str]:
        result = {}
        for arg in args:
            if "=" in arg:
                k, v = arg.split("=", 1)
                result[k.strip()] = v.strip()
        return result

    def _handle_run(self, args: list[str]) -> dict:
        if not args:
            return {"ok": False, "error": "Usage: /workflow run <name> [key=val ...]"}
        name = args[0]
        kwargs = self._parse_key_val(args[1:])
        return self._tools.workflow_run(name=name, args=kwargs, triggered_by="slash_command")

    def _handle_define(self, args: list[str]) -> dict:
        if not args:
            return {"ok": False, "error": "Usage: /workflow define <name>"}
        name = args[0]
        return {"ok": True, "name": name, "template": DEFAULT_TEMPLATE.format(name=name)}

    def _handle_list(self, args: list[str]) -> dict:
        return self._tools.workflow_list()

    def _handle_show(self, args: list[str]) -> dict:
        if not args:
            return {"ok": False, "error": "Usage: /workflow show <name>"}
        return self._tools.workflow_show(args[0])

    def _handle_delete(self, args: list[str]) -> dict:
        if not args:
            return {"ok": False, "error": "Usage: /workflow delete <name>"}
        return self._tools.workflow_delete(args[0])

    def _handle_history(self, args: list[str]) -> dict:
        name = args[0] if args else None
        return self._tools.workflow_history(workflow_name=name)

    def _handle_status(self, args: list[str]) -> dict:
        if not args:
            return {"ok": False, "error": "Usage: /workflow status <exec_id>"}
        return self._tools.workflow_status(args[0])

    def _handle_stop(self, args: list[str]) -> dict:
        if not args:
            return {"ok": False, "error": "Usage: /workflow stop <exec_id>"}
        return self._tools.workflow_stop(args[0])

    def _handle_rollback(self, args: list[str]) -> dict:
        if not args:
            return {"ok": False, "error": "Usage: /workflow rollback <exec_id>"}
        return self._tools.workflow_rollback(args[0])

    def _handle_export(self, args: list[str]) -> dict:
        if not args:
            return {"ok": False, "error": "Usage: /workflow export <name>"}
        fmt = "yaml"
        if len(args) > 1 and args[1] == "--json":
            fmt = "json"
        return self._tools.workflow_export(args[0], format=fmt)

    def _handle_import(self, args: list[str]) -> dict:
        if not args:
            return {"ok": False, "error": "Usage: /workflow import <yaml_string>"}
        return self._tools.workflow_import(" ".join(args))

    def _handle_suggest(self, args: list[str]) -> dict:
        return {"ok": True, "message": "Use workflow_suggest tool with recent messages for suggestions"}
