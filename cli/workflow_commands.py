"""Hermes CLI: `hermes workflow <verb>` commands."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from ..tools import workflow_tools as wt
from ..triggers.slash_command import WorkflowSlashDispatcher


def add_workflow_parser(subparsers) -> None:
    """Register `hermes workflow` subcommand. Called by Hermes CLI registration."""
    p = subparsers.add_parser("workflow", help="Dynamic workflow management")
    sub = p.add_subparsers(dest="workflow_verb", help="Workflow subcommand")

    # run
    run_p = sub.add_parser("run", help="Run a workflow")
    run_p.add_argument("name", help="Workflow name")
    run_p.add_argument("args", nargs="*", help="key=value arguments")

    # define
    def_p = sub.add_parser("define", help="Define a new workflow (opens editor)")
    def_p.add_argument("name", help="Workflow name")

    # list
    sub.add_parser("list", help="List all workflows")

    # show
    show_p = sub.add_parser("show", help="Show workflow definition")
    show_p.add_argument("name", help="Workflow name")

    # delete
    del_p = sub.add_parser("delete", help="Delete a workflow")
    del_p.add_argument("name", help="Workflow name")

    # history
    hist_p = sub.add_parser("history", help="Show execution history")
    hist_p.add_argument("name", nargs="?", help="Filter by workflow name")

    # status
    stat_p = sub.add_parser("status", help="Show execution status")
    stat_p.add_argument("exec_id", help="Execution ID")

    # stop
    stop_p = sub.add_parser("stop", help="Stop a running execution")
    stop_p.add_argument("exec_id", help="Execution ID")

    # rollback
    rb_p = sub.add_parser("rollback", help="Rollback an execution")
    rb_p.add_argument("exec_id", help="Execution ID")
    rb_p.add_argument("--to-version", type=int, dest="to_version", help="Rollback workflow definition to this version")

    # export
    exp_p = sub.add_parser("export", help="Export workflow as YAML")
    exp_p.add_argument("name", help="Workflow name")

    # diff
    diff_p = sub.add_parser("diff", help="Compare two versions of a workflow")
    diff_p.add_argument("name", help="Workflow name")
    diff_p.add_argument("v1", type=int, help="Version 1")
    diff_p.add_argument("v2", type=int, help="Version 2")

    # import
    imp_p = sub.add_parser("import", help="Import workflow from YAML")
    imp_p.add_argument("file", help="YAML file path")

    # metrics
    met_p = sub.add_parser("metrics", help="Show Prometheus-format metrics")
    met_p.add_argument("workflow", nargs="?", default=None, help="Filter by workflow name")

    p.set_defaults(func=_dispatch_workflow)


def _dispatch_workflow(args) -> dict[str, Any]:
    """Route parsed CLI args to workflow tools."""
    _ = WorkflowSlashDispatcher(wt)
    verb = getattr(args, "workflow_verb", None)

    if verb == "run":
        name = args.name
        kwargs = {}
        for a in getattr(args, "args", []):
            if "=" in a:
                k, v = a.split("=", 1)
                kwargs[k.strip()] = v.strip()
        return wt.workflow_run(name=name, args=kwargs, triggered_by="cli")

    if verb == "define":
        name = args.name
        result = (
            wt.workflow_show(name)
            if wt.workflow_show(name).get("ok")
            else {
                "ok": True,
                "yaml": f"# New workflow: {name}\n" + ("name: " + name + '\nversion: 1\ndescription: ""\nsteps: []\n'),
            }
        )
        # Write to temp file and open editor
        import os
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".yaml")
        with os.fdopen(fd, "w") as f:
            f.write(result.get("yaml", ""))
        # Whitelist EDITOR to prevent command injection; falls back to 'vi'
        allowed_editors = {"vi", "vim", "nano", "emacs", "code", "subl"}
        editor = os.environ.get("EDITOR", "vi")
        if editor not in allowed_editors:
            editor = "vi"
        subprocess.run([editor, path], check=True)  # noqa: S603
        with open(path) as f:
            yaml_content = f.read()
        os.unlink(path)
        return wt.workflow_define(name, yaml_content)

    if verb == "list":
        return wt.workflow_list()

    if verb == "show":
        return wt.workflow_show(args.name)

    if verb == "delete":
        return wt.workflow_delete(args.name)

    if verb == "history":
        return wt.workflow_history(getattr(args, "name", None))

    if verb == "status":
        return wt.workflow_status(args.exec_id)

    if verb == "stop":
        return wt.workflow_stop(args.exec_id)

    if verb == "rollback":
        return wt.workflow_rollback(args.exec_id, to_version=getattr(args, "to_version", None), triggered_by="cli")

    if verb == "export":
        return wt.workflow_export(args.name)

    if verb == "diff":
        return wt.workflow_diff(args.name, args.v1, args.v2)

    if verb == "import":
        with open(args.file) as f:
            return wt.workflow_import(f.read())

    if verb == "metrics":
        return wt.workflow_metrics(getattr(args, "workflow", None))

    return {"ok": False, "error": f"Unknown verb: {verb}"}


def print_result(result: dict):
    if result.get("ok"):
        if "yaml" in result:
            print(result["yaml"])
        elif "workflows" in result:
            for wf in result["workflows"]:
                print(f"  {wf['name']} (v{wf['version']})")
        elif "executions" in result:
            for e in result["executions"]:
                print(f"  {e['id']}  {e['workflow_id']}  {e['status']}  {e['started_at']}")
        elif "steps" in result:
            for s in result["steps"]:
                print(f"  [{s['index']}] {s['name']} ({s['type']}) → {s['status']}")
        elif "suggestions" in result:
            for s in result["suggestions"]:
                print(f"  → {s['suggest']}  {s['reason']}")
        elif "diff" in result:
            print(
                f"# {result['name']}: v{result['v1']} → v{result['v2']} (steps: {result['steps_v1']} → {result['steps_v2']})"
            )
            print(result["diff"])
        elif "metrics" in result:
            import json as _json

            print(_json.dumps(result["metrics"], indent=2, default=str))
        else:
            print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Error: {result.get('error', 'unknown error')}", file=sys.stderr)
        sys.exit(1)
