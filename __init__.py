"""
Hermes Dynamic Workflow Plugin

Provides Claude Code /workflow-style dynamic workflow execution for Hermes agent.

Plugin entry point — registers hooks, tools, and CLI commands.
"""

from __future__ import annotations

__version__ = "1.1.0"
__plugin_name__ = "hermes-dynamic-workflow"

# ── Plugin manifest ──────────────────────────────────────────────────────────────


def register(ctx: "PluginContext") -> None:  # type: ignore[name-defined]  # noqa: UP037,F821
    """
    Called by Hermes PluginManager on discovery.
    Register all hooks, tools, and CLI commands here.
    """
    import cli.workflow_commands as wc
    import tools.workflow_tools as wt
    from tools.schemas import SCHEMAS

    # Store plugin context for use in agent steps (delegate_task dispatch)
    wt._set_plugin_ctx(ctx)

    # Tool name → handler function mapping
    tool_handlers: dict[str, callable] = {
        "workflow_run": wt.workflow_run,
        "workflow_stop": wt.workflow_stop,
        "workflow_status": wt.workflow_status,
        "workflow_define": wt.workflow_define,
        "workflow_delete": wt.workflow_delete,
        "workflow_list": wt.workflow_list,
        "workflow_show": wt.workflow_show,
        "workflow_history": wt.workflow_history,
        "workflow_rollback": wt.workflow_rollback,
        "workflow_export": wt.workflow_export,
        "workflow_import": wt.workflow_import,
        "workflow_diff": wt.workflow_diff,
        "workflow_metrics": wt.workflow_metrics,
        "workflow_suggest": wt.workflow_suggest,
        "workflow_template_save": wt.workflow_template_save,
        "workflow_template_list": wt.workflow_template_list,
        "workflow_template_load": wt.workflow_template_load,
        "workflow_template_delete": wt.workflow_template_delete,
    }

    # Register tools with schema + handler (Hermes v0.21.0 keyword-arg API)
    for name, handler in tool_handlers.items():
        ctx.register_tool(
            name=name,
            toolset="workflow",
            schema=SCHEMAS[name],
            handler=handler,
            is_async=False,
            description="",
            emoji="🔁",
        )

    # Register in-session slash command (/workflow) — works in both CLI and gateway
    ctx.register_command(
        "workflow",
        handler_fn=_handle_workflow_command,
        description="Dynamic workflow management: run, define, list, stop, etc.",
        args_hint="<verb> [args]",
    )

    # Register terminal CLI command: hermes workflow <verb>
    ctx.register_cli_command(
        name="workflow",
        help="Dynamic workflow management (run, define, list, stop, etc.)",
        setup_fn=wc.add_workflow_parser,
        handler_fn=_handle_cli_workflow,
    )

    # Register hooks
    ctx.register_hook("pre_llm_call", _pre_llm_hook)
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_hook)

    # Ensure DB is initialized
    from storage.sqlite_store import ExecutionStore
    ExecutionStore()


# ── Slash command handler ───────────────────────────────────────────────────────


def _handle_workflow_command(raw_args: str) -> str | None:
    """Handle /workflow <verb> [args] from any session (CLI or gateway)."""
    import tools.workflow_tools as wt
    from triggers.slash_command import WorkflowSlashDispatcher

    dispatcher = WorkflowSlashDispatcher(wt)
    result = dispatcher.dispatch(raw_args)
    if result.get("ok"):
        return str(result)
    return f"Error: {result.get('error', 'unknown error')}"


def _handle_cli_workflow(args) -> None:
    """Handle `hermes workflow <verb>` terminal command (argparse Namespace)."""
    import cli.workflow_commands as wc
    result = wc.dispatch_workflow(args)
    wc.print_result(result)


# ── Hooks ─────────────────────────────────────────────────────────────────────


def _pre_llm_hook(
    session_id: str,
    user_message: str,
    conversation_history: list,
    is_first_turn: bool,
    model: str,
    platform: str,
    **kwargs,
) -> str | dict | None:
    """pre_llm_call: detect workflow intent from conversation and suggest."""
    messages = conversation_history or []
    if not messages:
        return None

    import triggers.intent_detector as idet
    suggestions = idet.detect_workflow_intent(messages)
    if not suggestions:
        return None

    msg = idet.make_suggestion_message(suggestions)
    if not msg:
        return None
    # Return dict form — injected into user message to preserve prompt caching
    return {"context": msg}


def _pre_gateway_hook(event, gateway, session_store, **kwargs) -> dict | None:
    """pre_gateway_dispatch: handle /workflow commands from gateway chat platforms."""
    text = getattr(event, "text", "") or ""
    if not text.startswith("/workflow"):
        return None

    args = text[len("/workflow "):] if text.startswith("/workflow ") else ""
    result = _handle_workflow_command(args)
    # Respond via gateway
    try:
        gateway.adapters[getattr(event.source, "platform", None)].send(
            {"text": result or "done"},
            session_id=getattr(event.source, "chat_id", None),
        )
    except Exception:  # noqa: BLE001
        pass  # swallow gateway send errors — command already executed
    return {"action": "skip"}  # prevent normal agent dispatch
