# Changelog

All notable changes to `hermes-dynamic-workflow` are documented here.

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
