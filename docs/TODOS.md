# TODOs — Future Considerations

## Thread Safety (Parallel Branches)

- [ ] `WorkflowContext` now uses a single `threading.Lock` protecting writes to `shared`, `pipeline`, `events`, `checkpoints`
- [ ] `execute_parallel_branch` uses `ThreadPoolExecutor` — this is correct for **tool-level parallelism** (API calls, I/O-bound work)
- [ ] `delegate_task` (LLM agent) integration with parallel branches is **not yet implemented** — see below

## Phase 2: LLM Agent Integration

### `delegate_task` / `execute_agent_step`

- `execute_agent_step` currently does not pass `permission_scope` — may need to add

### Parallel Branch Execution Model

Two modes exist at different layers — they should remain **decoupled**:

| Layer | Execution Model | How |
|-------|----------------|-----|
| Step execution | `ThreadPoolExecutor` | `execute_parallel_branch` — correct for I/O-bound tool calls |
| LLM agent | `delegate_task` | Separate concern — each agent is its own LLM call |

LLM can **recommend** which model to use when defining a workflow:
- `parallel_branch` with I/O tools → ThreadPoolExecutor (current)
- `parallel_branch` with independent LLM decisions → delegate_task (Phase 2)

Current recommendation: **LLM should default to ThreadPoolExecutor** unless specific agentic reasoning is needed per branch. All branches in a workflow should use the same execution model.

## Permission System

- [x] `WorkflowDefinition.permission_scope` added
- [x] YAML `permission: {allowed_tools, blocked_tools}` support
- [x] `PermissionScope.from_workflow_definition()` factory
- [x] `execute_tool_step` enforces `can_run_tool()` before execution
- [ ] `allowed_tools=None` (open) is the default — document this behavior
- [ ] `execute_agent_step` does NOT enforce `permission_scope` — agents may need a separate permission model

## SAGA / Rollback Semantics (Clarified)

- `StepErrorAction.ROLLBACK` on a step: execute SAGA compensation inline, workflow **continues** to remaining steps
- `RollbackPolicy.SAGA`: compensation runs when ROLLBACK action triggered
- `RollbackPolicy.CHECKPOINT`: context restored to last checkpoint, workflow continues
- `StepErrorAction.STOP`: immediate failure, no compensation, workflow terminates

## Schema / Validation

- [x] `compensate.tool` validated non-empty in `CompensateConfig.from_dict()`
- [x] `compensate` validated in `validate_workflow()` (YAML layer)
- [x] `agent` step type schema validation — `agent.profile` or `agent.goal` required
- [x] `ParallelBranch` circular dependency validation — cycles detected at validation time

## O(n²) in_degree

- [x] `dependents` map pre-built — O(n) per step completion update
- [x] in_degree init still O(n²) — acceptable (one-time at workflow start)
- Note: `definition.steps.index(step)` in checkpoint call is O(n) but pre-snap is already done, so this is not in the hot path
