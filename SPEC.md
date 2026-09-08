# Hermes Dynamic Workflow Plugin — SPEC

## 1. Overview

**Plugin name:** `hermes-dynamic-workflow`
**Type:** General plugin (drop-in to `~/.hermes/plugins/` or `plugins/`)
**Purpose:** Provide Claude Code `/workflow`-like dynamic workflow execution for Hermes agent.

Core idea: Users can define workflows via natural language or YAML, execute them with full observability, auto-recovery on failure, and share/export templates.

---

## 2. Architecture

```
hermes-dynamic-workflow/
├── plugin.yaml                    # Plugin manifest
├── __init__.py                   # Entry point, registers hooks/commands
├── workflow/
│   ├── __init__.py
│   ├── core.py                   # WorkflowEngine, Step, WorkflowState
│   ├── definitions.py            # Workflow YAML schema + parser
│   ├── executor.py               # Execution engine (sequential/parallel branches)
│   ├── state.py                  # SQLite-backed state, checkpoints, rollback
│   ├── versioned_store.py        # Version control for workflow definitions
│   ├── context.py                # Shared state context (pipeline + shared state)
│   ├── error_handling.py         # Error strategies (fail-fast, retry, degrade, saga)
│   ├── events.py                # Event bus for step completion signals
│   └── security.py              # Permission scoping, audit log
├── triggers/
│   ├── __init__.py
│   ├── slash_command.py          # /workflow, /wf run/list/define...
│   └── intent_detector.py        # AI intent detection hook → suggest workflow
├── storage/
│   ├── __init__.py
│   ├── sqlite_store.py           # WorkflowDefinitionStore, ExecutionStore
│   └── templates.py             # Template registry + import/export
├── observability/
│   ├── __init__.py
│   ├── logger.py                 # Structured SQLite log + Prometheus metrics
│   └── trace.py                  # OpenTelemetry spans
├── cli/
│   ├── __init__.py
│   └── workflow_commands.py      # hermes workflow <verb> CLI
├── tools/
│   ├── __init__.py
│   ├── workflow_tools.py         # workflow_run, workflow_stop, workflow_status...
│   └── delegates.py             # step_agent_spawn, step_tool_call
└── tests/
    ├── test_workflow_engine.py
    ├── test_state.py
    ├── test_error_handling.py
    └── test_versioned_store.py
```

---

## 3. Data Model

### WorkflowDefinition (SQLite `workflow_definitions`)
```sql
CREATE TABLE workflow_definitions (
  id TEXT PRIMARY KEY,           -- uuid
  name TEXT NOT NULL UNIQUE,
  version INTEGER NOT NULL DEFAULT 1,
  definition_yaml TEXT NOT NULL, -- raw YAML source
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  created_by TEXT,               -- user identifier
  tags TEXT,                    -- JSON list
  is_template INTEGER DEFAULT 0
);
CREATE TABLE workflow_versions (
  id TEXT PRIMARY KEY,
  workflow_id TEXT REFERENCES workflow_definitions(id),
  version INTEGER NOT NULL,
  definition_yaml TEXT NOT NULL,
  changed_at TEXT NOT NULL,
  changed_by TEXT,
  change_summary TEXT
);
```

### WorkflowExecution (SQLite `workflow_executions`)
```sql
CREATE TABLE workflow_executions (
  id TEXT PRIMARY KEY,
  workflow_id TEXT REFERENCES workflow_definitions(id),
  version INTEGER NOT NULL,
  status TEXT NOT NULL,          -- running|paused|completed|failed|rolled_back|terminated
  context_json TEXT,             -- serialized shared state
  started_at TEXT NOT NULL,
  ended_at TEXT,
  triggered_by TEXT,             -- slash_command|intent_detection|event|cron|api
  triggered_by_user TEXT,
  concurrency_mode TEXT DEFAULT 'sequential',
  max_duration_seconds INTEGER,
  error_policy TEXT,             -- fail_fast|retry|degrade|manual
  rollback_policy TEXT           -- none|checkpoint|saga|version
);
CREATE TABLE execution_steps (
  id TEXT PRIMARY KEY,
  execution_id TEXT REFERENCES workflow_executions(id),
  step_index INTEGER NOT NULL,
  step_name TEXT NOT NULL,
  step_type TEXT NOT NULL,       -- tool|agent|step|event
  input_json TEXT,
  output_json TEXT,
  status TEXT NOT NULL,          -- pending|running|completed|failed|skipped|rolled_back
  started_at TEXT,
  ended_at TEXT,
  error TEXT,
  retry_count INTEGER DEFAULT 0,
  checkpoint_json TEXT           -- snapshot after step for rollback
);
CREATE TABLE audit_log (
  id TEXT PRIMARY KEY,
  execution_id TEXT REFERENCES workflow_executions(id),
  step_id TEXT,
  action TEXT NOT NULL,
  actor TEXT,
  details_json TEXT,
  timestamp TEXT NOT NULL
);
```

### WorkflowSchema (YAML DSL)
```yaml
name: ci-check
version: 1
description: Run CI pipeline for a PR
concurrency: sequential  # sequential|parallel|hybrid
max_duration: 3600      # seconds, 0 = no limit
error_policy: retry     # fail_fast|retry|degrade|manual
rollback_policy: checkpoint  # none|checkpoint|saga|version

context:
  pr_number: "{{ trigger.pr_number }}"
  repo: "{{ trigger.repo }}"

steps:
  - name: checkout
    type: tool
    tool: bash
    args:
      command: "git checkout {{ context.pr_branch }}"
    on_error: rollback  # rollback|skip|continue|stop

  - name: analyze
    type: agent
    agent:
      profile: analyst
      goal: "Analyze the code changes in PR #{{ context.pr_number }}"
    requires: [checkout]

  - name: build
    type: tool
    tool: bash
    args:
      command: "make ci-build"
    requires: [analyze]
    retry:
      max_attempts: 3
      backoff: exponential
    on_error: degrade

  - name: test
    type: parallel_branch
    branches:
      - name: unit_tests
        steps:
          - type: tool
            tool: bash
            args: { command: "make test-unit" }
      - name: integration_tests
        steps:
          - type: tool
            tool: bash
            args: { command: "make test-integration" }
    requires: [build]

  - name: report
    type: agent
    agent:
      profile: reporter
      goal: "Summarize CI results and post to PR"
    requires: [test]
    on_complete: checkpoint
```

---

## 4. Trigger System

### 4.1 Slash Commands
```
/workflow run <name> [args]   # Run named workflow
/workflow define <name>       # Define new workflow (opens editor with YAML template)
/workflow list                 # List all workflows
/workflow show <name>          # Show workflow definition
/workflow delete <name>        # Delete a workflow
/workflow history              # Show execution history
/workflow status <exec_id>     # Show execution status
/workflow stop <exec_id>       # Terminate execution
/workflow rollback <exec_id>   # Rollback execution
/workflow export <name>        # Export as JSON/YAML
/workflow import <file>       # Import from JSON/YAML
/workflow suggest              # AI suggests workflows based on conversation
```

### 4.2 Intent Detection Hook
- Hook: `pre_llm_call` or `on_message` analyzes conversation
- Pattern match on keywords + semantic similarity
- Suggestion shown to user as a prompt to confirm launch

---

## 5. Execution Engine

### 5.1 Step Types
| Step Type | Handler | Description |
|-----------|---------|-------------|
| `tool` | `step_tool_call` | Call Hermes tool (bash, read, edit, grep...) |
| `agent` | `step_agent_spawn` | Spawn sub-agent via `delegate_task` |
| `parallel_branch` | `step_parallel` | Fan out steps in parallel, join on completion |
| `event` | `step_event` | Emit event to trigger other workflows |
| `checkpoint` | `step_checkpoint` | Manually save checkpoint |

### 5.2 Concurrency Modes
- `sequential`: steps run one after another
- `parallel`: all steps without `requires` run concurrently
- `hybrid`: `parallel_branch` nodes fan out, rest sequential

### 5.3 Error Handling Per Step
- `stop`: fail workflow immediately
- `skip`: mark step as skipped, continue
- `continue`: mark failed, continue to next
- `rollback`: trigger rollback chain
- `degrade`: skip branch, continue main path
- `retry`: re-attempt with backoff

### 5.4 State Context
```python
class WorkflowContext:
    workflow_id: str
    execution_id: str
    shared: dict  # all steps read/write here
    pipeline: list  # ordered output list (last = latest)
    checkpoints: list  # list of {step_index, snapshot}
    events: list  # emitted events
```

---

## 6. State & Rollback

### 6.1 Checkpoint Rollback
After each step completes: serialize `WorkflowContext` + step outputs → `checkpoint_json`.
On rollback: deserialize last checkpoint, reset `shared` + `pipeline`, mark steps after checkpoint as `rolled_back`.

### 6.2 Saga Compensation
Steps can declare `compensate` function:
```yaml
- name: deploy
  type: tool
  tool: kubectl
  args: { command: "apply -f deployment.yaml" }
  compensate:
    tool: kubectl
    args: { command: "rollback deployment.yaml" }
```
On failure after `deploy`, execute its `compensate` in reverse order.

### 6.3 Version Rollback
`workflow_versions` table stores all definition versions. Rollback to version N re-runs from scratch with that definition.

---

## 7. Version Control

- Every `workflow define` update increments version
- Full YAML stored in `workflow_versions`
- `change_summary` (auto-generated or user-provided)
- `workflow rollback <exec_id> --to-version <N>` supported
- `workflow diff <name>` to compare versions

---

## 8. Observability

### 8.1 Structured Log (SQLite `audit_log`)
Every action: step start/complete/fail, context update, checkpoint save, rollback trigger, user action.

### 8.2 Prometheus Metrics
```
hermes_workflow_executions_total{status}
hermes_workflow_execution_duration_seconds{workflow_name}
hermes_workflow_step_duration_seconds{step_type}
hermes_workflow_active_runs
hermes_workflow_retries_total{workflow_name}
```

### 8.3 OpenTelemetry Trace
Span per execution, child spans per step with attributes: step_name, step_type, status, retry_count.

### 8.4 CLI Status Panel
```
hermes workflow status
┌─ Running Workflows ──────────────────────┐
│ wf-001  ci-check     running  00:03:21   │
│ wf-002  deploy-prod  paused   00:01:45   │
└──────────────────────────────────────────┘
```

---

## 9. Security

- Workflows run with caller's Hermes permission scope
- Destructive operations (`rm`, `kubectl delete`, `DROP TABLE`) → require explicit `--confirm` flag
- Audit log: who triggered which workflow when
- Concurrent workflow isolation: each gets a separate process group
- Resource limits: `max_duration`, `max_steps`, `max_parallel_branches`

---

## 10. CLI Integration

```bash
# Define workflow via editor
hermes workflow define my-ci
# Opens $EDITOR with YAML template

# Run
hermes workflow run my-ci pr_number=123 repo=foo/bar

# List
hermes workflow list

# Status
hermes workflow status wf-001

# Stop
hermes workflow stop wf-001

# Rollback
hermes workflow rollback wf-001

# Export/Import
hermes workflow export my-ci > my-ci.yaml
hermes workflow import my-ci.yaml

# Suggest
hermes workflow suggest  # AI reads last N messages, suggests workflows
```

---

## 11. Interaction Modes

| Mode | Trigger | UX |
|------|---------|-----|
| Slash command | `/workflow run ...` | CLI immediate |
| Natural language | "run the ci-check workflow" | AI parses intent, asks confirm |
| AI-suggested | hook detects pattern | "I suggest running ci-check. Start?" |
| TUI | `hermes workflow tui` | Full-screen workflow dashboard |
| API / hook | `workflow_run` tool from code | Programmatic |

---

## 12. Persistence & Sharing

- **Local SQLite**: `~/.hermes/workflows.db`
- **Project-bound**: `.hermes/workflows/<name>.yaml` in repo (git-tracked)
- **Export**: JSON or YAML file
- **Import**: from file or URL
- **Template registry**: `~/.hermes/workflow_templates/` — community-curated

---

## 13. Plugin Integration Points

| Hermes System | Integration | How |
|---------------|-------------|-----|
| Tool registry | `ctx.register_tool()` | Register `workflow_*` tools |
| CLI registry | `ctx.register_cli_command()` | Register `hermes workflow` subcommands |
| Hooks | `pre_llm_call`, `on_message` | Intent detection |
| Delegate task | `delegate_task` call | Agent step spawns sub-agent |
| Memory provider | `MemoryProvider` ABC | Optional: store workflow context in Hermes memory |
| Cron | `cronjob` tool / `hermes cron` | Schedule workflow execution |
| Kanban | kanban integration | Workflows as kanban tasks |

---

## 14. File Structure (Plugin Drop-in)

```
~/.hermes/plugins/hermes-dynamic-workflow/
├── plugin.yaml
├── __init__.py
├── workflow/
├── triggers/
├── storage/
├── observability/
├── cli/
├── tools/
└── tests/
```

Discovery: `PluginManager` scans `~/.hermes/plugins/*/plugin.yaml`.

---

## 15. Implementation Phases

### Phase 1: Core Engine (MVP)
- `plugin.yaml` + `__init__.py` skeleton
- `WorkflowDefinition` YAML parser
- `WorkflowEngine` sequential executor
- `workflow_run` + `workflow_stop` tools
- SQLite schema + `WorkflowExecution` CRUD
- `/workflow run` slash command

### Phase 2: Error Handling + State
- Checkpoint save/restore
- Retry with backoff
- Manual rollback
- `workflow_status` tool

### Phase 3: Parallel + Agents
- `parallel_branch` executor
- `agent` step type via `delegate_task`
- `hybrid` concurrency mode

### Phase 4: Versioning + Rollback
- `workflow_versions` table
- `workflow_rollback` command + tool
- Version diff

### Phase 5: Observability
- Prometheus metrics
- OpenTelemetry traces
- `workflow history` + status panel

### Phase 6: Intent Detection + Suggestion
- `pre_llm_call` hook
- Suggestion to user
- TUI dashboard

### Phase 7: Templates + Sharing
- Export/Import
- Template registry
- Project-bound workflows
