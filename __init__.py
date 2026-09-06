"""
Hermes Dynamic Workflow Plugin

Provides Claude Code /workflow-style dynamic workflow execution for Hermes agent.

Plugin entry point — registers hooks, tools, and CLI commands.
"""

from __future__ import annotations

# ── Plugin manifest ──────────────────────────────────────────────────────────────


__version__ = "1.0.0"
__plugin_name__ = "hermes-dynamic-workflow"

# ── Hermes plugin entry point ───────────────────────────────────────────────────


def register(plugin_ctx: "PluginContext") -> None:  # type: ignore[name-defined]  # noqa: UP037,F821
    """
    Called by Hermes PluginManager on discovery.
    Register all hooks, tools, and CLI commands here.
    """
    # Import here to avoid hard dep at import time (Hermes may not be fully loaded)
    import cli.workflow_commands as wc
    import tools.workflow_tools as wt

    # ── Register tools ────────────────────────────────────────────────────────
    for tool_fn in [
        wt.workflow_run,
        wt.workflow_stop,
        wt.workflow_status,
        wt.workflow_define,
        wt.workflow_delete,
        wt.workflow_list,
        wt.workflow_show,
        wt.workflow_history,
        wt.workflow_rollback,
        wt.workflow_export,
        wt.workflow_import,
        wt.workflow_diff,
        wt.workflow_metrics,
        wt.workflow_suggest,
        wt.workflow_template_save,
        wt.workflow_template_list,
        wt.workflow_template_load,
        wt.workflow_template_delete,
    ]:
        plugin_ctx.register_tool(tool_fn.__name__, tool_fn)

    # ── Register CLI subcommand ───────────────────────────────────────────────
    plugin_ctx.register_cli_command("workflow", wc.add_workflow_parser, wc.print_result)

    # ── Register hooks ────────────────────────────────────────────────────────
    plugin_ctx.register_hook("pre_llm_call", _pre_llm_hook)
    plugin_ctx.register_hook("on_message", _on_message_hook)

    # ── Ensure DB is initialized ───────────────────────────────────────────────
    from storage.sqlite_store import ExecutionStore
    ExecutionStore()  # ensure schema created on first access


# ── Hooks ─────────────────────────────────────────────────────────────────────


def _pre_llm_hook(hook_ctx: "HookContext") -> None:  # type: ignore[name-defined]  # noqa: UP037,F821
    """pre_llm_call: detect workflow intent from conversation and suggest."""
    messages = getattr(hook_ctx, "messages", [])
    if not messages:
        return

    import triggers.intent_detector as idet
    suggestions = idet.detect_workflow_intent(messages)
    if suggestions:
        msg = idet.make_suggestion_message(suggestions)
        if msg:
            hook_ctx.add_system_message(msg)


def _on_message_hook(hook_ctx: "HookContext") -> None:  # type: ignore[name-defined]  # noqa: UP037,F821
    """on_message: detect /workflow slash command and dispatch."""
    content = getattr(hook_ctx, "content", "") or ""
    if not content.startswith("/workflow"):
        return

    import tools.workflow_tools as wt
    from triggers.slash_command import WorkflowSlashDispatcher

    dispatcher = WorkflowSlashDispatcher(wt)
    result = dispatcher.dispatch(content)

    if result.get("ok"):
        # Respond inline with result
        hook_ctx.set_response(str(result))
    else:
        hook_ctx.set_error(result.get("error", "Unknown error"))


# ── CLI integration ─────────────────────────────────────────────────────────────


def add_workflow_subparser(subparsers):
    """Exposed for Hermes CLI registration."""
    import cli.workflow_commands as wc
    wc.add_workflow_parser(subparsers)
    return wc.print_result
