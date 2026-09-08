# Changelog

All notable changes to `hermes-dynamic-workflow` are documented here.

## [Unreleased] — 2026-09-07

### Breaking — Hermes v0.21.0 API Compatibility

- **`register_tool()` signature** — now uses keyword args (`name=`, `toolset=`, `schema=`, `handler=`) per Hermes plugin API. All 15 tools registered with OpenAI function-call schemas.
- **`on_message` hook removed** — replaced by `ctx.register_command("workflow", ...)` for in-session `/workflow` slash commands (works in CLI and gateway).
- **`pre_llm_call` return** — now returns `{"context": msg}` instead of calling non-existent `hook_ctx.add_system_message()`.
- **`pre_gateway_dispatch` added** — handles `/workflow` commands from gateway chat platforms (Telegram, Discord, etc.).
- **Tool schemas** — all 15 tools now have proper OpenAI function-call schemas in `tools/schemas.py`.
- **`delegate_task` integration** — agent steps now use `ctx.dispatch_tool("delegate_task", {...})` via stored `PluginContext`, replacing the non-existent `tools.delegate_tool` import. Falls back to direct import if no `plugin_ctx`.
- **Dual command registration** — both `ctx.register_command()` (`/workflow` slash) and `ctx.register_cli_command()` (`hermes workflow` terminal) now registered for full CLI + gateway support.

## [1.1.0] — 2026-09-07

### Security

- **Shell injection guard** — `execute_tool_step` now uses `shell=False` + `shlex.split` for subprocess fallback, eliminating shell injection vectors. Shell operators (`$()`, backtick, `;`, `&&`, `||`, `>>`, `<<`) are blocked as defense-in-depth even when shlex parsing is bypassed.
- **Command allowlist** — `SHELL_SAFE_COMMANDS` whitelist for subprocess fallback. Only read-only/safe commands (`ls`, `cat`, `grep`, `git`, `curl`, etc.) are permitted; `python`/`perl`/`ruby` excluded to prevent inline code execution.
- **Workflow permission scope** — `permission.allowed_tools` / `permission.blocked_tools` per workflow definition; enforced at tool dispatch time.
- **Echo rm detection** — patterns like `echo rm -rf` are caught and blocked; the echo prefix is stripped before destructive pattern matching.
- **Prometheus label escaping** — `workflow_executions_total` / `workflow_retries_total` now escape `\`, `"`, newlines in label values to prevent metric injection.
- **Template name validation** — `WorkflowTemplate.save()` enforces `^[a-zA-Z][-a-zA-Z0-9_]*$` to prevent path traversal and injection via template names.

### Validation

- **YAML multi-document guard** — `parse_workflow_yaml` rejects workflows with multiple YAML documents (must be a single document).
- **Permission block validation** — `permission` field must be a dict; `allowed_tools` / `blocked_tools` must be lists.
- **Compensate step validation** — `compensate.tool` must be a non-empty string; `compensate.args` must be a dict.

### Bug Fixes

- **`echo rm` detection** — `is_destructive` now strips `echo` prefix before pattern matching, catching `echo rm -rf /` style obfuscation.
- **Parallel rollback tracking** — `_pending_dependents` map pre-built to avoid O(n²) scan on each step completion.

## [1.0.0] — 2025-09-06

### Added

- **18 workflow tools** registered with Hermes plugin system:
  `workflow_run`, `workflow_stop`, `workflow_status`, `workflow_define`,
  `workflow_delete`, `workflow_list`, `workflow_show`, `workflow_history`,
  `workflow_rollback`, `workflow_diff`, `workflow_export`, `workflow_import`,
  `workflow_metrics`, `workflow_suggest`,
  `workflow_template_save`, `workflow_template_list`,
  `workflow_template_load`, `workflow_template_delete`

- **Execution engine** (`workflow/executor.py`):
  - Kahn's algorithm topological sort — correct parallel dependency resolution
  - `stop_event` parameter for race-free termination via `workflow_stop`
  - `max_duration` enforcement — workflow auto-terminates after configured seconds
  - `resume_from_step` parameter for checkpoint-based resume after rollback
  - OpenTelemetry `trace_step` span wrapping every step
  - `execute_checkpoint_step` stores `compensate` config in checkpoint metadata

- **Tool step execution** — calls Hermes tool registry, falls back to subprocess for shell commands. Result written to `WorkflowContext.shared` and `pipeline`.

- **Agent step execution** — calls `delegate_task` with resolved `profile` and `goal`. Result written to context.

- **Parallel branch** — `ThreadPoolExecutor` fan-out, all sub-branches run concurrently, join on completion. Checkpoint persisted to DB after completion.

- **Error handling** — `fail_fast`, `skip`, `continue`, `rollback`, `degrade`, `retry` (exponential/linear backoff). `SKIP` action properly updates in-degree graph so dependents unblock.

- **Saga rollback** — `compensate` functions executed in reverse topological order before restoring checkpoint context.

- **VersionedStore** (`workflow/versioned_store.py`):
  - `diff(name, v1, v2)` — unified diff between two definition versions
  - `rollback_definition(name, to_version)` — restore old version as new version
  - `list_versions(name)` — version history

- **workflow_rollback** — full checkpoint restore with `resume_from_step` replay; `--to-version N` flag for definition rollback + re-run

- **workflow_diff** — compare two workflow versions, returns unified diff

- **workflow_metrics** — Prometheus-format metrics (`workflow_executions_total`, `workflow_execution_duration_seconds`, `workflow_active_runs`)

- **Template registry** (`storage/templates.py`) — `TemplateRegistry` class with save/list/load/delete/export_all/import_from_file

- **`/workflow` slash command** dispatcher — all verbs routed to corresponding tools

- **Intent detector** (`triggers/intent_detector.py`) — keyword → workflow suggestion on `pre_llm_call` and `on_message` hooks

- **CLI commands**: `run`, `define`, `list`, `show`, `delete`, `status`, `stop`, `rollback`, `diff`, `export`, `import`, `metrics`, `suggest`, `template-list`, `template-load`, `template-save`, `template-delete`

- **AuditLogWriter** — thread-safe write to `audit_log` table on every step and workflow event

- **OpenTelemetry traces** — `trace_workflow` span per execution, `trace_step` span per step (gracefully stubs if OTel unavailable)

- **Prometheus metrics** — queryable via `workflow_metrics` tool/CLI

- All `datetime.utcnow()` → `datetime.now(timezone.utc)` (Python 3.12+ deprecation fix)
