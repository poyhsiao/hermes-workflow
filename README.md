# Hermes Dynamic Workflow Plugin

`hermes-dynamic-workflow` provides Claude Code `/workflow`-style dynamic workflow execution for the Hermes agent. Users define workflows in YAML, execute them with full observability, auto-recovery on failure, checkpoint/restore, and share via templates.

## Features

- **YAML workflow definitions** — declare steps, dependencies, error policies, and rollback strategies in a single file
- **Sequential / Parallel / Hybrid concurrency** — Kahn's algorithm topological sort ensures dependencies are always respected
- **Tool step** — calls any Hermes tool (bash, read, edit, grep, ...) with `{{ context }}` variable substitution
- **Agent step** — spawns a sub-agent via `delegate_task`
- **Parallel branch** — fan-out sub-branches with thread-pool execution, join on completion
- **Checkpoint + Saga rollback** — persist context after each step; rollback restores shared state and replays from last checkpoint
- **Version control** — every workflow update is versioned; diff and rollback to any prior version
- **Intent detection** — `pre_llm_call` hook suggests workflows from conversation keywords
- **Observability** — SQLite audit log, Prometheus metrics (`workflow_executions_total`, `workflow_active_runs`, ...), OpenTelemetry spans per step
- **Template registry** — save, list, load, delete reusable workflow templates from `~/.hermes/workflow_templates/`
- **Security** — workflow permission scopes (`allowed_tools`/`blocked_tools`), command allowlist for subprocess fallback, defense-in-depth shell operator blocking, Prometheus label escaping

## Installation

Drop this plugin into your Hermes plugins directory:

```bash
# If Hermes scans ~/.hermes/plugins/
cp -r hermes-dynamic-workflow ~/.hermes/plugins/

# Or point to a custom plugins directory via HERMES_PLUGIN_PATH
```

Verify it loads:

```bash
hermes plugins list
# → hermes-dynamic-workflow v1.0.0
```

## Quick Start

### 1. Define a workflow

```bash
hermes workflow define my-ci
# Opens $EDITOR with the YAML template
```

Example `my-ci.yaml`:

```yaml
name: my-ci
version: 1
description: Run CI pipeline for a PR
concurrency: sequential
max_duration: 3600
error_policy: retry
rollback_policy: checkpoint

context:
  pr_number: ""
  repo: ""

steps:
  - name: checkout
    type: tool
    args:
      tool: bash
      command: "git checkout $PR_BRANCH"
    on_error: rollback

  - name: build
    type: tool
    args:
      tool: bash
      command: "make ci-build"
    requires: [checkout]
    retry:
      max_attempts: 3
      backoff: exponential

  - name: test
    type: parallel_branch
    branches:
      - name: unit_tests
        steps:
          - type: tool
            args: { tool: bash, command: "make test-unit" }
      - name: integration_tests
        steps:
          - type: tool
            args: { tool: bash, command: "make test-integration" }
    requires: [build]
```

### 2. Run it

```bash
hermes workflow run my-ci pr_number=123 repo=foo/bar
# → {"ok": true, "execution_id": "abc-123", "status": "running", "name": "my-ci"}
```

### 3. Check status

```bash
hermes workflow status abc-123
# → [0] checkout (tool) → completed  [1] build (tool) → completed  [2] test (parallel_branch) → running
```

### 4. Stop / Rollback

```bash
hermes workflow stop abc-123
hermes workflow rollback abc-123
hermes workflow rollback abc-123 --to-version 2  # rollback definition to v2 and re-run
```

## CLI Reference

| Command | Description |
|---------|-------------|
| `hermes workflow run <name> [k=v ...]` | Run workflow with context args |
| `hermes workflow define <name>` | Define new workflow (opens editor) |
| `hermes workflow list` | List all workflows |
| `hermes workflow show <name>` | Show workflow YAML |
| `hermes workflow status <exec_id>` | Show step execution status |
| `hermes workflow stop <exec_id>` | Terminate a running execution |
| `hermes workflow history [name]` | Show execution history |
| `hermes workflow rollback <exec_id>` | Rollback to last checkpoint |
| `hermes workflow rollback <exec_id> --to-version N` | Rollback definition to version N |
| `hermes workflow diff <name> v1 v2` | Diff two workflow versions |
| `hermes workflow export <name>` | Export as YAML |
| `hermes workflow import <file>` | Import from YAML file |
| `hermes workflow metrics [name]` | Prometheus-format metrics |
| `hermes workflow suggest` | AI suggests workflows from conversation |
| `hermes workflow template-list` | List saved templates |
| `hermes workflow template-load <name>` | Load template YAML |
| `hermes workflow template-save <name> <yaml>` | Save as template |
| `hermes workflow template-delete <name>` | Delete a template |

## Tools API

All CLI commands are also available as Hermes tools:

```
workflow_run, workflow_stop, workflow_status, workflow_define,
workflow_delete, workflow_list, workflow_show, workflow_history,
workflow_rollback, workflow_export, workflow_import, workflow_diff,
workflow_metrics, workflow_suggest,
workflow_template_save, workflow_template_list,
workflow_template_load, workflow_template_delete
```

## YAML Schema

```yaml
name: string              # Required. Unique workflow name.
version: integer         # Default 1. Auto-incremented on update.
description: string
concurrency: sequential | parallel | hybrid
max_duration: integer    # Seconds. 0 = no limit.
error_policy: fail_fast | retry | degrade | manual
rollback_policy: none | checkpoint | saga | version

context:                  # Variable schema, populated at runtime
  var_name: default_value

steps:
  - name: string         # Required. Unique within workflow.
    type: tool | agent | parallel_branch | event | checkpoint
    args: {}             # Passed to the tool/agent
    requires: [step_name, ...]   # Dependency names
    on_error: stop | skip | continue | rollback | degrade | retry
    retry:
      max_attempts: integer
      backoff: exponential | linear
      backoff_base: float    # Default 2.0
      initial_delay: float    # Default 1.0 seconds
    compensate:              # For saga rollback
      tool: string
      args: {}
    agent:
      profile: string
      goal: string
    branches: []         # For parallel_branch type
    event_name: string   # For event type
```

## Architecture

```
hermes-dynamic-workflow/
├── __init__.py              # Plugin entry point
├── plugin.yaml              # Manifest (version, hooks, tools, commands)
├── workflow/
│   ├── core.py              # WorkflowDefinition, ExecutionRecord, WorkflowEngine
│   ├── definitions.py        # YAML parser + validator
│   ├── executor.py          # Sequential/parallel/hybrid execution engine
│   ├── context.py           # WorkflowContext (shared state + pipeline)
│   ├── error_handling.py    # Retry/backoff strategies
│   ├── events.py            # EventBus (STEP_STARTED, STEP_COMPLETED, ...)
│   └── security.py          # PermissionScope, destructive-op guard, AuditLogger
├── storage/
│   ├── sqlite_store.py      # WorkflowDefinition + Execution CRUD
│   └── templates.py         # TemplateRegistry (disk-based)
├── observability/
│   ├── logger.py            # AuditLogWriter, Prometheus metrics
│   └── trace.py             # OpenTelemetry spans (stub if OTel unavailable)
├── tools/
│   └── workflow_tools.py    # 18 workflow_* tools
├── triggers/
│   ├── slash_command.py     # /workflow dispatcher
│   └── intent_detector.py  # Keyword → workflow suggestion
└── cli/
    └── workflow_commands.py # hermes workflow <verb> CLI
```

## Data Storage

- **Workflow DB**: `~/.hermes/workflows.db` (SQLite)
  - `workflow_definitions` — current definition per workflow
  - `workflow_versions` — full YAML per version
  - `workflow_executions` — one row per run
  - `execution_steps` — per-step input/output/status/retry/checkpoint
  - `audit_log` — every action timestamped
- **Templates**: `~/.hermes/workflow_templates/*.yaml`

## Security

Workflows can declare a `permission` block to restrict which tools and commands are permitted:

```yaml
permission:
  allowed_tools:   # optional allowlist — only these tools can run
    - bash
    - read
    - grep
  blocked_tools:   # optional blocklist — these tools are always denied
    - write
    - edit
```

**Command allowlist** — when a tool is not found in the Hermes registry, the subprocess fallback is used. Only safe read-only commands are permitted (`ls`, `cat`, `grep`, `stat`, `md5sum`, `sha256sum`, etc.).

**Defense-in-depth** — shell operators (`;`, `&&`, `||`, `$()`, backtick, `>>`, `<<`) are blocked even when `shell=False` is used. Commands like `rm -rf`, `dd`, `mkfs`, `curl | bash` are blocked. `echo rm -rf` style obfuscation is also caught.

## Error Handling

| Action | Behavior |
|--------|----------|
| `stop` | Fail workflow immediately |
| `skip` | Mark step skipped, continue to dependents |
| `continue` | Mark failed, continue next independent steps |
| `rollback` | Trigger checkpoint restore, stop |
| `degrade` | Skip branch, continue main path |
| `retry` | Re-attempt with exponential/linear backoff |

## Rollback Policies

- **`checkpoint`** — restore `shared`, `pipeline`, `events` from last step's snapshot
- **`saga`** — execute `compensate` functions in reverse order, then restore checkpoint
- **`version`** — rollback definition to prior version, re-run from scratch

## Prometheus Metrics

```
# HELP hermes_workflow_executions_total Total workflow executions by workflow and status
# TYPE hermes_workflow_executions_total gauge
hermes_workflow_executions_total{workflow="ci-check",status="completed"} 42

# HELP hermes_workflow_execution_duration_seconds Average execution duration
# TYPE hermes_workflow_execution_duration_seconds gauge
hermes_workflow_execution_duration_seconds{workflow="ci-check"} 147.3

# HELP hermes_workflow_active_runs Number of currently running executions
# TYPE hermes_workflow_active_runs gauge
hermes_workflow_active_runs 3
```

## Development

```bash
# Run tests
python3 -m pytest tests/ -v

# Run with coverage
python3 -m pytest tests/ --cov=workflow --cov=storage --cov=observability
```

## License

Apache License 2.0
