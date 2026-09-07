"""YAML workflow definition parser and validator."""

from __future__ import annotations

import yaml

from workflow.core import WorkflowDefinition


class ValidationError(Exception):
    pass


def parse_workflow_yaml(yaml_str: str) -> WorkflowDefinition:
    try:
        docs = list(yaml.safe_load_all(yaml_str))
    except yaml.YAMLError as e:
        raise ValidationError(f"Invalid YAML: {e}") from e

    if len(docs) == 0:
        raise ValidationError("Empty YAML document")
    if len(docs) > 1:
        raise ValidationError(
            f"Multiple YAML documents ({len(docs)}) found; workflow must be a single document"
        )

    data = docs[0]
    if data is None or not isinstance(data, dict):
        raise ValidationError("YAML document must be a non-null dict")
    validate_workflow(data)
    return WorkflowDefinition.from_dict(data, yaml_str)


_WF_KNOWN_FIELDS: frozenset[str] = frozenset({
    "name", "version", "description", "concurrency", "max_duration",
    "error_policy", "rollback_policy", "permission", "steps",
    "context", "context_schema",
})
_WF_STEP_KNOWN_FIELDS: frozenset[str] = frozenset({
    "name", "type", "requires", "args", "on_error", "retry",
    "compensate", "agent", "parallel_branch", "event", "checkpoint",
    "branches",
})
_LEGACY_STEP_FIELDS: frozenset[str] = frozenset({
    "tool", "command", "agent_goal", "agent_profile", "profile", "goal", "event_name",
})


def validate_workflow(data: dict) -> None:
    errors: list[str] = []

    # Normalize context_schema to context
    if "context_schema" in data and "context" not in data:
        data["context"] = data.pop("context_schema")
    elif "context_schema" in data:
        data.pop("context_schema")

    # Reject unknown top-level fields (prevents malicious YAML keys from being silently ignored)
    unknown = sorted(set(data.keys()) - _WF_KNOWN_FIELDS)
    if unknown:
        errors.append(f"Unknown top-level field(s): {unknown}")

    if "name" not in data:
        errors.append("Missing required field: 'name'")
    if "steps" not in data:
        errors.append("Missing required field: 'steps'")
    elif not isinstance(data["steps"], list):
        errors.append("'steps' must be a list")

    # Validate permission block
    perm = data.get("permission")
    if perm is not None and not isinstance(perm, dict):
        errors.append("'permission' must be a dict")
    elif perm:
        if "allowed_tools" in perm and not isinstance(perm["allowed_tools"], list):
            errors.append("'permission.allowed_tools' must be a list")
        if "blocked_tools" in perm and not isinstance(perm["blocked_tools"], list):
            errors.append("'permission.blocked_tools' must be a list")

    if "steps" in data and isinstance(data["steps"], list):
        step_names = set()
        all_step_names = set()
        for i, step in enumerate(data["steps"]):
            if not isinstance(step, dict):
                errors.append(f"Step {i} must be a dict")
                continue
            if "name" not in step:
                errors.append(f"Step {i}: missing 'name'")
            name = step.get("name", "")
            all_step_names.add(name)

        for i, step in enumerate(data["steps"]):
            if not isinstance(step, dict):
                continue
            # Normalize legacy step fields into supported shapes
            if "tool" in step or "command" in step:
                tool_val = step.pop("tool", None) or step.pop("command", None)
                if tool_val is not None:
                    step.setdefault("args", {})["tool"] = tool_val
            if "agent_goal" in step or "agent_profile" in step or "profile" in step or "goal" in step:
                agent = step.setdefault("agent", {})
                if "agent_goal" in step:
                    agent.setdefault("goal", step.pop("agent_goal", None))
                if "agent_profile" in step:
                    agent.setdefault("profile", step.pop("agent_profile", None))
                if "profile" in step:
                    agent.setdefault("profile", step.pop("profile", None))
                if "goal" in step:
                    agent.setdefault("goal", step.pop("goal", None))
            if "event_name" in step:
                step.setdefault("event", step.pop("event_name", None))
            # Reject unknown step fields
            unknown_step = sorted(set(step.keys()) - _WF_STEP_KNOWN_FIELDS - _LEGACY_STEP_FIELDS)
            if unknown_step:
                errors.append(f"Step {i}: unknown field(s): {unknown_step}")
            name = step.get("name", "")
            if name in step_names:
                errors.append(f"Duplicate step name: '{name}'")
            step_names.add(name)

            step_type = step.get("type", "tool")
            valid_types = {"tool", "agent", "parallel_branch", "event", "checkpoint"}
            if step_type not in valid_types:
                errors.append(f"Step '{name}': invalid type '{step_type}'; must be one of {valid_types}")

            # Validate requires references against all step names (allows forward references)
            for dep in step.get("requires", []):
                if dep not in all_step_names:
                    errors.append(f"Step '{name}': requires unknown step '{dep}'")

            # parallel_branch must have branches
            if step_type == "parallel_branch":
                branches = step.get("branches", [])
                if not branches:
                    errors.append(f"Step '{name}': parallel_branch requires 'branches'")
                for b in branches:
                    if "name" not in b or "steps" not in b:
                        errors.append(f"Step '{name}': branch missing 'name' or 'steps'")

            # agent must have profile or goal
            if step_type == "agent":
                agent = step.get("agent", {})
                if not agent.get("profile") and not agent.get("goal"):
                    errors.append(f"Step '{name}': agent requires 'agent.profile' or 'agent.goal'")

            # compensate validation - runs whenever value is present and non-null (including empty mappings)
            compensate = step.get("compensate")
            if compensate is not None:
                if not isinstance(compensate, dict):
                    errors.append(f"Step '{name}': 'compensate' must be a dict")
                elif not compensate.get("tool") or not isinstance(compensate.get("tool"), str) or not compensate["tool"].strip():
                    errors.append(f"Step '{name}': compensate.tool must be a non-empty string")
                elif "args" not in compensate or not isinstance(compensate["args"], dict):
                    errors.append(f"Step '{name}': compensate.args must be a dict")

            # retry validation
            retry = step.get("retry")
            if retry:
                if not isinstance(retry, dict):
                    errors.append(f"Step '{name}': 'retry' must be a dict")
                elif "max_attempts" in retry and (not isinstance(retry["max_attempts"], int) or retry["max_attempts"] < 1):
                    errors.append(f"Step '{name}': retry.max_attempts must be a positive int")

    if errors:
        raise ValidationError("\n".join(errors))


def dump_workflow_yaml(definition: WorkflowDefinition) -> str:
    data = definition.to_dict()
    return yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
