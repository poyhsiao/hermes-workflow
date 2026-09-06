# Hermes Dynamic Workflow Plugin — Implementation Plan

## Repository Layout

```
hermes-dynamic-workflow/           ← plugin root (symlink or drop into ~/.hermes/plugins/)
├── plugin.yaml                   # Manifest
├── __init__.py                   # Plugin entry point
├── workflow/
│   ├── __init__.py
│   ├── core.py                   # WorkflowEngine, Step, WorkflowState
│   ├── definitions.py            # YAML parser, schema validation
│   ├── executor.py               # Sequential + parallel execution
│   ├── state.py                  # WorkflowContext, checkpoint/rollback
│   ├── versioned_store.py        # Version CRUD, rollback to version
│   ├── context.py               # Shared + pipeline state
│   ├── error_handling.py        # Error strategies
│   ├── events.py                # Event bus
│   └── security.py              # Permission scoping, audit
├── triggers/
│   ├── __init__.py
│   ├── slash_command.py         # /workflow <verb> dispatcher
│   └── intent_detector.py       # pre_llm_call hook → suggestion
├── storage/
│   ├── __init__.py
│   ├── sqlite_store.py          # Definition + Execution stores
│   └── templates.py             # Import/export templates
├── observability/
│   ├── __init__.py
│   ├── logger.py                # Structured audit log
│   └── trace.py                # OpenTelemetry wrapper
├── cli/
│   ├── __init__.py
│   └── workflow_commands.py     # hermes workflow <verb>
├── tools/
│   ├── __init__.py
│   └── workflow_tools.py        # workflow_run, workflow_stop, workflow_status...
└── tests/
    ├── __init__.py
    ├── test_workflow_engine.py
    ├── test_definitions.py
    ├── test_state.py
    └── test_versioned_store.py
```

## Phase 1: Skeleton + YAML Parser + MVP Executor

### 1.1 `plugin.yaml`
```yaml
name: hermes-dynamic-workflow
version: "1.0.0"
description: Dynamic workflow execution (Claude Code /workflow style)
author: YOU
platforms: [cli, tui, desktop]
hermes_version: ">=0.1.0"
entry_point: __init__.py
hooks:
  - pre_llm_call
  - on_message
tools:
  - workflow_run
  - workflow_stop
  - workflow_status
  - workflow_define
  - workflow_delete
  - workflow_list
  - workflow_show
  - workflow_history
  - workflow_rollback
  - workflow_export
  - workflow_import
  - workflow_suggest
commands:
  - workflow
```

### 1.2 `__init__.py`
```python
# Entry point
# - register all tools via ctx.register_tool()
# - register CLI command via ctx.register_cli_command()
# - register hooks (pre_llm_call, on_message)
# - register slash command "workflow"
```

### 1.3 `workflow/core.py`
```python
@dataclass
class Step:
    name: str
    step_type: str  # tool|agent|parallel_branch|event|checkpoint
    args: dict
    requires: list[str] = field(default_factory=list)
    on_error: str = "stop"  # stop|skip|continue|rollback|degrade|retry
    retry: dict = None
    compensate: dict = None

@dataclass
class WorkflowDefinition:
    name: str
    version: int
    description: str
    concurrency: str = "sequential"
    max_duration: int = 0
    error_policy: str = "fail_fast"
    rollback_policy: str = "checkpoint"
    context: dict = field(default_factory=dict)
    steps: list[Step] = field(default_factory=list)

class WorkflowEngine:
    def __init__(self, definition: WorkflowDefinition, context: WorkflowContext, store: ExecutionStore):
        self.definition = definition
        self.context = context
        self.store = store

    def run(self) -> ExecutionResult: ...
    def stop(self): ...
    def get_status(self) -> ExecutionStatus: ...
```

### 1.4 `workflow/definitions.py`
- YAML schema using `dataclasses` + `yaml.safe_load`
- Validate required fields, step types, references
- `parse_workflow_yaml(yaml_str) -> WorkflowDefinition`

### 1.5 `workflow/executor.py`
```python
def execute_steps(steps: list[Step], context: WorkflowContext, store: ExecutionStore):
    # Build dependency graph (topological sort)
    # Identify steps with no requires → initial batch
    # sequential: run one by one
    # parallel: run initial batch concurrently (threading)
    # Await each batch, collect outputs → feed to next batch
    # Handle on_error per step
```

### 1.6 `workflow/state.py`
```python
class WorkflowContext:
    workflow_id: str
    execution_id: str
    shared: dict
    pipeline: list
    checkpoints: list[dict]

    def checkpoint(self, step_index: int, store: ExecutionStore): ...
    def rollback(self, checkpoint: dict): ...
```

### 1.7 `storage/sqlite_store.py`
- `init_schema()`: create tables if not exist
- `WorkflowDefinitionStore`: CRUD for `workflow_definitions` + `workflow_versions`
- `ExecutionStore`: CRUD for `workflow_executions` + `execution_steps` + `audit_log`
- `get_execution(id) -> WorkflowExecution`

### 1.8 `tools/workflow_tools.py`
```python
@tool
def workflow_run(name: str, args: dict = None, **kwargs) -> WorkflowExecutionResult:
    # 1. Load definition from store
    # 2. Build initial context from args + trigger
    # 3. Create Execution record in store
    # 4. Spawn executor (background thread or async)
    # 5. Return execution_id

@tool
def workflow_stop(execution_id: str) -> dict: ...

@tool
def workflow_status(execution_id: str) -> dict: ...
```

### 1.9 `triggers/slash_command.py`
```
/workflow run <name> [key=val ...]
/workflow define <name>          # open editor with YAML template
/workflow list
/workflow show <name>
/workflow delete <name>
/workflow history
/workflow status <exec_id>
/workflow stop <exec_id>
/workflow rollback <exec_id>
/workflow export <name>
/workflow import <file>
/workflow suggest
```

---

## Phase 2: Error Handling + Checkpoint/Retry

### 2.1 `workflow/error_handling.py`
```python
class ErrorStrategy(ABC):
    @abstractmethod
    def handle(self, step: Step, error: Exception, ctx: WorkflowContext, store: ExecutionStore) -> ErrorAction: ...

class FailFast(ErrorStrategy): ...
class Retry(ErrorStrategy):
    def __init__(self, max_attempts: int, backoff: str): ...
class Degrade(ErrorStrategy): ...
class Manual(ErrorStrategy): ...
class SagaCompensate(ErrorStrategy): ...
```

### 2.2 `workflow/state.py` — rollback
```python
class WorkflowContext:
    def checkpoint(self, step_index: int, store: ExecutionStore):
        store.save_checkpoint(self.execution_id, step_index, {
            "shared": copy.deepcopy(self.shared),
            "pipeline": list(self.pipeline),
            "step_index": step_index
        })

    def rollback_to(self, checkpoint: dict, store: ExecutionStore):
        self.shared = checkpoint["shared"]
        self.pipeline = checkpoint["pipeline"]
        # Mark steps after checkpoint step as rolled_back in store
```

---

## Phase 3: Parallel Branches + Agent Steps

### 3.1 `workflow/executor.py` — parallel_branch
```python
def execute_parallel_branch(branch: Step, ctx: WorkflowContext, store: ExecutionStore):
    with ThreadPoolExecutor(max_workers=len(branch.branches)) as executor:
        futures = {executor.submit(execute_steps, b.steps, ctx, store): b for b in branch.branches}
        for future in as_completed(futures):
            branch_result = future.result()
            ctx.shared[f"{branch.name}.{futures[future].name}"] = branch_result
```

### 3.2 `workflow/tools/step_tool_call.py`
```python
def step_tool_call(step: Step, ctx: WorkflowContext) -> any:
    # Use Hermes tool registry to call the tool
    # Inject ctx.shared as context variables into tool args
    # Pipe: previous step output → current step input if no explicit input
```

### 3.3 `workflow/tools/step_agent_spawn.py`
```python
def step_agent_spawn(step: Step, ctx: WorkflowContext) -> any:
    # Call delegate_task with step.agent.profile + goal
    # Pass relevant context as prompt
    # Collect result → ctx.shared[step.name]
```

---

## Phase 4: Version Control

### 4.1 `workflow/versioned_store.py`
```python
def save_version(workflow_id: str, definition_yaml: str, changed_by: str = None, summary: str = None):
    # Increment version, insert into workflow_versions

def get_version(workflow_id: str, version: int) -> WorkflowDefinition: ...

def list_versions(workflow_id: str) -> list[VersionRecord]: ...

def rollback_to_version(execution_id: str, version: int) -> WorkflowExecutionResult:
    # Load old definition → re-run execution with it
```

---

## Phase 5: Observability

### 5.1 `observability/logger.py`
```python
def log_audit(execution_id: str, step_id: str, action: str, actor: str = None, details: dict = None):
    # Insert into audit_log table

def get_prometheus_metrics() -> dict:
    # Query execution stats → Prometheus format
```

### 5.2 `observability/trace.py`
```python
from opentelemetry import trace
tracer = trace.get_tracer(__name__)

def trace_workflow(execution_id: str):
    with tracer.start_as_current_span(f"workflow/{execution_id}") as span:
        # child spans per step
```

---

## Phase 6: Intent Detection

### 6.1 `triggers/intent_detector.py`
```python
async def on_message hook:
    # Read last N messages
    # Compute embedding or keyword match against registered workflow names/descriptions
    # If confidence > threshold:
    #   ctx.send_suggestion(f"I notice you might want to run '{matched_workflow.name}'. Start?")
    #   → set workflow_suggestion in session state
```

---

## Phase 7: Templates + Sharing

### 7.1 `storage/templates.py`
```python
def export_workflow(name: str, format: str = "yaml") -> str:
    # Load from store → serialize to YAML or JSON

def import_workflow(yaml_or_json_str: str, as_template: bool = False) -> str:
    # Parse → validate → save to store
    # If as_template: mark is_template=1
```

---

## File Creation Order (Implementation Sequence)

```
1. plugin.yaml
2. __init__.py
3. workflow/__init__.py
4. workflow/core.py
5. workflow/definitions.py
6. workflow/context.py
7. workflow/error_handling.py
8. workflow/events.py
9. workflow/security.py
10. storage/__init__.py
11. storage/sqlite_store.py
12. workflow/state.py
13. workflow/executor.py
14. workflow/versioned_store.py
15. tools/__init__.py
16. tools/workflow_tools.py
17. triggers/__init__.py
18. triggers/slash_command.py
19. triggers/intent_detector.py
20. observability/__init__.py
21. observability/logger.py
22. observability/trace.py
23. cli/__init__.py
24. cli/workflow_commands.py
25. tests/__init__.py
26. tests/test_workflow_engine.py
27. tests/test_definitions.py
28. tests/test_state.py
29. tests/test_versioned_store.py
```

---

## Key Dependencies

- `pyyaml` — YAML parsing
- `python-dotenv` — env config
- `opentelemetry-api` + `opentelemetry-sdk` — tracing
- `structlog` or stdlib `logging` — structured logs
- `tenacity` — retry with backoff
- Python stdlib: `sqlite3`, `threading`, `concurrent.futures`, `uuid`, `datetime`

No new external dependencies beyond stdlib + already-Hermes-available packages.

---

## Testing Strategy

- Unit test YAML parser (roundtrip: YAML → WorkflowDefinition → YAML)
- Unit test topological sort for dependency graph
- Unit test error strategies in isolation
- Unit test checkpoint save/restore
- Integration test: full workflow run via `workflow_run` tool
- Integration test: rollback mid-execution
- Integration test: parallel_branch steps
