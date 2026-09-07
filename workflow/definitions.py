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
    validate_workflow(data)
    return WorkflowDefinition.from_dict(data, yaml_str)


def validate_workflow(data: dict) -> None:
    errors: list[str] = []

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
        for i, step in enumerate(data["steps"]):
            if not isinstance(step, dict):
                errors.append(f"Step {i} must be a dict")
                continue
            if "name" not in step:
                errors.append(f"Step {i}: missing 'name'")
            name = step.get("name", "")
            if name in step_names:
                errors.append(f"Duplicate step name: '{name}'")
            step_names.add(name)

            step_type = step.get("type", "tool")
            valid_types = {"tool", "agent", "parallel_branch", "event", "checkpoint"}
            if step_type not in valid_types:
                errors.append(f"Step '{name}': invalid type '{step_type}'; must be one of {valid_types}")

            # Validate requires references
            for dep in step.get("requires", []):
                if dep not in step_names and dep not in [s["name"] for s in data["steps"]]:
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

            # compensate validation
            compensate = step.get("compensate")
            if compensate:
                if not isinstance(compensate, dict):
                    errors.append(f"Step '{name}': 'compensate' must be a dict")
                elif not compensate.get("tool") or not isinstance(compensate.get("tool"), str) or not compensate["tool"].strip():
                    errors.append(f"Step '{name}': compensate.tool must be a non-empty string")
                elif not compensate.get("args") or not isinstance(compensate.get("args"), dict):
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
