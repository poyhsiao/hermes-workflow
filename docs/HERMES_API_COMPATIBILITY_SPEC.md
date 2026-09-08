# Hermes v0.21.0 Plugin API Compatibility Spec

## Brainstorm Findings

### 關鍵確認

**1. `ctx.register_cli_command()` signature (Hermes v0.21.0):**
```python
ctx.register_cli_command(
    name="my-plugin",
    help="...",
    setup_fn=_setup_argparse,
    handler_fn=_my_command,
)
```
Current `__init__.py` 用的是 `(name, parser_fn, output_fn)` — **wrong arity and param names**.

**2. `ctx.register_command()` — 一次搞定 CLI + Gateway:**
```python
ctx.register_command("workflow", handler_fn, description="...", args_hint="<verb> [args]")
```
This handles `/workflow` in **both** CLI and gateway sessions via one registration. No `on_message` hook, no `pre_gateway_dispatch` needed for slash commands.

**3. `ctx.dispatch_tool(name, args)` 可從 hook callback 呼叫:**
```python
ctx.dispatch_tool("delegate_task", {"goal": ..., "context": ...})
```
Returns result string. **Risk**: Tool handler 執行時是否能拿到 `ctx`？從範例看 `ctx` 是 register closure 來的。如果 tool handler 要在 execution time 呼叫 `dispatch_tool`，需要把 `ctx` 存到 tool handler closure 或 `ctx.state` 裡。

---

## Risk Log

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Tool handler 拿不到 `ctx` 無法呼叫 `dispatch_tool` | **Low** | High | 用 module-level global 存 ctx（Hermes closure pattern）；已驗證 `kwargs` 只有 task_id，無 ctx，但鉤在 `register()` closure 可存取 |
| `register_cli_command` handler_fn signature | Medium | Gateway deploy 時 CLI 命令失敗 | 先查 Hermes 源码确认 handler receives argparse.Namespace |
| 15 個 schema 全補可能漏引數格式 | Low | Tool 跑不起來 | 每個 schema 先驗證符合 `type: "function"` + function 格式 |

---

## Status

**All 5 incompatibilities resolved.** Implementation complete as of 2026-09-07.

| # | Area | Status |
|---|------|--------|
| 1 | `register_tool()` schema-based | ✅ Fixed — `tools/schemas.py` + keyword-arg registration |
| 2 | `delegate_task` via Hermes | ✅ Fixed — `ctx.dispatch_tool()` with `_plugin_ctx` module global |
| 3 | `pre_llm_call` return | ✅ Fixed — returns `{"context": msg}` |
| 4 | `/workflow` dispatch | ✅ Fixed — both `register_command()` + `register_cli_command()` |
| 5 | Tool schemas | ✅ Fixed — all 18 tools have OpenAI function-call schemas |

### Phase 5 — Integration Test

Manual verification required in live Hermes environment:

```bash
# 1. Tool registration
/hermes tools  # should list all 15 workflow_* tools

# 2. Slash command
/workflow list
/workflow run test-workflow

# 3. pre_llm_call hook
# Say "run the deploy workflow" — should get suggestion

# 4. Gateway (if applicable)
# Send "/workflow list" from Telegram/Discord
```

### Files Changed

| File | Change |
|------|--------|
| `tools/schemas.py` | **NEW** — 18 OpenAI function-call schemas |
| `__init__.py` | Keyword-arg `register_tool()`, dual command reg, fixed hooks |
| `workflow/executor.py` | `plugin_ctx` param, `ctx.dispatch_tool()` for agent steps |
| `tools/workflow_tools.py` | `_plugin_ctx` global, `plugin_ctx` threaded to `execute_steps` |
| `plugin.yaml` | Removed `on_message`, added `pre_gateway_dispatch` |
| `CHANGELOG.md` | Updated with all changes |

---

## Incompatibility Matrix

| # | Area | Current (Wrong) | Hermes v0.21.0 (Correct) |
|---|------|-----------------|---------------------------|
| 1 | `register_tool()` | `ctx.register_tool(name, fn)` — positional, function ref | `ctx.register_tool(name=, toolset=, schema=, handler=, ...)` — keyword args + schema dict |
| 2 | `delegate_task` | Own `Thread` pool in `workflow_tools.py` | `ctx.dispatch_tool("delegate_task", {"goal": ...})` — uses Hermes built-in |
| 3 | `pre_llm_call` return | `hook_ctx.add_system_message(msg)` | Return `{"context": text}` or `str` — injected into user message |
| 4 | `/workflow` dispatch | `on_message` hook (doesn't exist) | CLI command registered via `register_cli_command` only; gateway uses `pre_gateway_dispatch` |
| 5 | Tool schema | Raw Python functions | Proper OpenAI function-call schema dict |

---

## 1. `register_tool()` — Schema-Based Registration

### Problem

Current code:
```python
plugin_ctx.register_tool(tool_fn.__name__, tool_fn)
```
Passes name + function directly. Hermes requires a **schema dict** (OpenAI function-call format) and keyword-only args.

### Fix

Each tool needs a companion schema dict and must be registered with keyword args:

```python
# Before
plugin_ctx.register_tool("workflow_run", workflow_run)

# After
plugin_ctx.register_tool(
    name="workflow_run",
    toolset="workflow",
    schema={
        "type": "function",
        "function": {
            "name": "workflow_run",
            "description": "Start a named workflow execution with optional context overrides.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Workflow name to execute."},
                    "context_overrides": {"type": "object", "description": "Override workflow context values."},
                },
                "required": ["name"],
            },
        },
    },
    handler=_handler_workflow_run,   # plain function, receives args dict
    is_async=False,
    description="Start a workflow execution",
    emoji="🔁",
)
```

Handler signature: `def handler(args: dict, **kwargs) -> str`

All 18 tools in `tools/workflow_tools.py` need schemas. See Appendix A for full schema definitions.

---

## 2. `delegate_task` — Use Hermes Built-In

### Problem

Current implementation spawns a `Thread` directly in `workflow_tools.workflow_run()` when the step type is `agent`.

### Fix

Remove the `Thread` pool. Instead, when an `agent` step is encountered during execution, call:

```python
result = ctx.dispatch_tool("delegate_task", {
    "goal": f"Execute workflow step: {step.name}",
    "context": {...step definition and context...},
})
```

This requires the plugin to have access to the `PluginContext` at runtime (passed from the hook context). The current architecture stores engines globally — this must change: the `WorkflowEngine` must receive `ctx` at construction time so it can call `dispatch_tool` from within step execution.

**Architectural change:** `WorkflowEngine` and `execute_steps()` need a `ctx` field. The `ctx` is obtained from the hook at the top level and threaded through.

---

## 3. `pre_llm_call` Return Value

### Problem

Current:
```python
def _pre_llm_hook(hook_ctx):
    suggestions = detect_workflow_intent(messages)
    if suggestions:
        msg = make_suggestion_message(suggestions)
        hook_ctx.add_system_message(msg)  # WRONG — this API doesn't exist
```

### Fix

Return a dict with `context` key (string is also allowed but dict preserves prompt caching):

```python
def _pre_llm_hook(session_id, user_message, conversation_history, is_first_turn, model, platform, **kwargs):
    messages = conversation_history or []
    suggestions = detect_workflow_intent(messages)
    if not suggestions:
        return None
    msg = make_suggestion_message(suggestions)
    return {"context": msg}  # injected into user message by Hermes
```

The `HookContext.add_system_message()` call — which never existed in Hermes — is removed entirely.

---

## 4. `/workflow` Command Dispatch — No `on_message` Hook

### Problem

Current:
```python
plugin_ctx.register_hook("on_message", _on_message_hook)
```

`on_message` is not a valid Hermes hook name. There is no message-intercept hook in the CLI.

### Fix

The `/workflow` slash command is a **CLI command**, not a message hook. The correct approach:

1. **CLI registration** is already correct — `register_cli_command("workflow", ...)` works, but the current implementation tries to call it from a non-existent `on_message` hook.

2. **Remove** the `on_message` hook registration entirely.

3. **Keep** `register_cli_command("workflow", ...)` — this works when the user types `/workflow` at the CLI prompt.

4. **Gateway users** (`/workflow` typed in a chat platform): This needs `pre_gateway_dispatch` to intercept messages starting with `/workflow` and dispatch them. But this is a **gateway-only** hook — in CLI mode the slash command handler above is sufficient.

**Decision:** If the plugin should work in gateway mode too, add a `pre_gateway_dispatch` hook that handles `/workflow` text commands. If only CLI mode is needed, remove `on_message` hook and keep only the CLI command registration.

```python
# For gateway support:
def _pre_gateway_hook(event, gateway, session_store, **kwargs):
    text = getattr(event, "text", "") or ""
    if not text.startswith("/workflow"):
        return None  # allow normal dispatch
    # Parse and dispatch via CLI command path
    ...

def register(ctx):
    ctx.register_cli_command("workflow", add_workflow_parser, print_result)
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_hook)  # only if gateway needed
```

---

## 5. Tool Schemas — OpenAI Function-Call Format

### Problem

Current tools are raw Python functions. Hermes needs explicit OpenAI function-call schemas for the model to know tool names, descriptions, and parameters.

### Fix

Every tool in `tools/workflow_tools.py` needs a companion schema constant in the exact format:

```python
WORKFLOW_RUN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workflow_run",
        "description": "...",
        "parameters": {
            "type": "object",
            "properties": {...},
            "required": [...],
        },
    },
}
```

See Appendix A for all 15 tool schemas.

---

## Implementation Phases

### Phase 1: Tool Schema Registration (Breaking)
- Add schema dicts to all 18 tools in `tools/workflow_tools.py`
- Change `plugin_ctx.register_tool(name, fn)` → keyword-arg form
- Verify tools appear in Hermes `/tools` list

### Phase 2: Remove `on_message` Hook
- Delete `_on_message_hook` and its registration
- Keep CLI command registration (it works without the hook)
- If gateway support needed: implement `pre_gateway_dispatch` instead

### Phase 3: Fix `pre_llm_call` Return
- Change hook signature to use Hermes kwargs
- Replace `hook_ctx.add_system_message()` with `return {"context": msg}`
- Delete non-existent `HookContext` method call

### Phase 4: `delegate_task` via `dispatch_tool`
- Pass `ctx` from hook registration down to `WorkflowEngine`
- Replace `Thread` spawning in agent steps with `ctx.dispatch_tool("delegate_task", {...})`
- Remove `_engines` global dict, use Hermes-managed context

### Phase 5: Integration Test
- Run full test suite
- Manual: `/workflow run test`, `/workflow list`, etc.
- Verify delegation works via Hermes thread management

---

## Files to Change

| File | Change |
|------|--------|
| `__init__.py` | Fix `register_tool` calls, remove `on_message` hook, fix `pre_llm_call` return |
| `tools/workflow_tools.py` | Add schemas for all 15 tools, change handler signatures to `def handler(args, **kwargs)` |
| `triggers/slash_command.py` | Unchanged (CLI command dispatcher) |
| `triggers/intent_detector.py` | Unchanged (logic is fine) |
| `workflow/core.py` | Accept `ctx` in `WorkflowEngine.__init__`, remove direct `Thread` pool |
| `workflow/executor.py` | Thread `ctx` through `execute_steps` |

---

## Appendix A: Tool Schemas

All 18 tools that need schemas:

```python
WORKFLOW_RUN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workflow_run",
        "description": "Start a named workflow execution.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Workflow name to execute."},
                "context_overrides": {"type": "object", "description": "Override workflow context values (key=value)."},
            },
            "required": ["name"],
        },
    },
}

WORKFLOW_STOP_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workflow_stop",
        "description": "Stop a running workflow execution.",
        "parameters": {
            "type": "object",
            "properties": {
                "execution_id": {"type": "string", "description": "Execution ID to stop."},
            },
            "required": ["execution_id"],
        },
    },
}

WORKFLOW_STATUS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workflow_status",
        "description": "Get the status of a workflow execution.",
        "parameters": {
            "type": "object",
            "properties": {
                "execution_id": {"type": "string", "description": "Execution ID to query."},
            },
            "required": ["execution_id"],
        },
    },
}

WORKFLOW_DEFINE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workflow_define",
        "description": "Define or update a workflow from YAML.",
        "parameters": {
            "type": "object",
            "properties": {
                "yaml": {"type": "string", "description": "YAML workflow definition."},
            },
            "required": ["yaml"],
        },
    },
}

WORKFLOW_DELETE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workflow_delete",
        "description": "Delete a workflow definition.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Workflow name to delete."},
            },
            "required": ["name"],
        },
    },
}

WORKFLOW_LIST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workflow_list",
        "description": "List all workflow definitions.",
        "parameters": {"type": "object", "properties": {}},
    },
}

WORKFLOW_SHOW_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workflow_show",
        "description": "Show a workflow definition.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Workflow name."},
            },
            "required": ["name"],
        },
    },
}

WORKFLOW_HISTORY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workflow_history",
        "description": "Get execution history for a workflow.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Workflow name."},
                "limit": {"type": "integer", "description": "Max results (default 10)."},
            },
            "required": ["name"],
        },
    },
}

WORKFLOW_ROLLBACK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workflow_rollback",
        "description": "Rollback a failed execution to last checkpoint.",
        "parameters": {
            "type": "object",
            "properties": {
                "execution_id": {"type": "string", "description": "Execution ID to rollback."},
            },
            "required": ["execution_id"],
        },
    },
}

WORKFLOW_EXPORT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workflow_export",
        "description": "Export a workflow definition as YAML.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Workflow name."},
                "format": {"type": "string", "enum": ["yaml", "json"], "description": "Export format."},
            },
            "required": ["name"],
        },
    },
}

WORKFLOW_IMPORT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workflow_import",
        "description": "Import a workflow from YAML.",
        "parameters": {
            "type": "object",
            "properties": {
                "yaml": {"type": "string", "description": "YAML workflow definition."},
            },
            "required": ["yaml"],
        },
    },
}

WORKFLOW_DIFF_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workflow_diff",
        "description": "Compare two versions of a workflow.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Workflow name."},
                "v1": {"type": "integer", "description": "First version number."},
                "v2": {"type": "integer", "description": "Second version number."},
            },
            "required": ["name", "v1", "v2"],
        },
    },
}

WORKFLOW_METRICS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workflow_metrics",
        "description": "Get execution metrics for a workflow.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Workflow name."},
                "period": {"type": "string", "description": "Time period (e.g. '7d', '30d')."},
            },
            "required": ["name"],
        },
    },
}

WORKFLOW_SUGGEST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workflow_suggest",
        "description": "Suggest workflows based on recent conversation context.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max suggestions (default 3)."},
            },
        },
    },
}

WORKFLOW_TEMPLATE_SAVE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workflow_template_save",
        "description": "Save a workflow as a reusable template.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Template name."},
                "yaml": {"type": "string", "description": "YAML content."},
            },
            "required": ["name", "yaml"],
        },
    },
}

WORKFLOW_TEMPLATE_LIST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workflow_template_list",
        "description": "List all saved workflow templates.",
        "parameters": {"type": "object", "properties": {}},
    },
}

WORKFLOW_TEMPLATE_LOAD_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workflow_template_load",
        "description": "Load a saved template's YAML content.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Template name."},
            },
            "required": ["name"],
        },
    },
}

WORKFLOW_TEMPLATE_DELETE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "workflow_template_delete",
        "description": "Delete a saved template.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Template name."},
            },
            "required": ["name"],
        },
    },
}
```

---

## Appendix B: Reference — Hermes PluginContext API

```
register_tool(name=, toolset=, schema=, handler=, check_fn=, requires_env=, is_async=, description=, emoji=, override=)
register_hook(hook_name, callback_fn)
register_cli_command(name, parser_fn, output_fn)
dispatch_tool(name, args)   # invoke any registered tool
inject_message(content, role=, session_key=)
profile_name: str
```

**Hook callback signatures:**
- `pre_llm_call(session_id, user_message, conversation_history, is_first_turn, model, platform, **kwargs)` → `str | dict | None`
- `post_llm_call(...)` → same
- `pre_tool_call(tool_name, tool_args, **kwargs)` → `None`
- `post_tool_call(tool_name, tool_args, result, **kwargs)` → `None`
- `on_session_start(session_id, model, platform, **kwargs)` → `None`
- `on_session_end(session_id, **kwargs)` → `None`
- `pre_gateway_dispatch(event, gateway, session_store, **kwargs)` → `None | dict` (action=skip/rewrite/allow)
