"""Intent detection hook: pre_llm_call + on_message to suggest workflows."""

from __future__ import annotations

# Keyword → workflow name hints
KEYWORD_HINTS = {
    "ci": "ci-check",
    "test": "run-tests",
    "deploy": "deploy",
    "build": "build",
    "release": "release",
    "backup": "backup",
    "migrate": "migrate-db",
    "lint": "lint",
    "format": "format-code",
    "audit": "security-audit",
    "sync": "sync-data",
    "report": "generate-report",
}


def detect_workflow_intent(messages: list[dict]) -> list[dict]:
    """Analyze recent messages for workflow trigger patterns. Returns suggestions."""
    if not messages:
        return []

    suggestions = []
    # Look at last 3 messages
    recent = messages[-3:]
    combined_text = " ".join(
        m.get("content", "") if isinstance(m, dict) else str(m)
        for m in recent
    ).lower()

    for keyword, workflow_name in KEYWORD_HINTS.items():
        if keyword in combined_text:
            suggestions.append({
                "workflow": workflow_name,
                "reason": f"'{keyword}' detected in conversation",
                "suggest": f"/workflow run {workflow_name}",
            })

    return suggestions


def make_suggestion_message(suggestions: list[dict]) -> str | None:
    """Format suggestions into a user-facing prompt."""
    if not suggestions:
        return None
    lines = ["**Workflow suggestions:**"]
    for s in suggestions[:3]:
        lines.append(f"- `{s['suggest']}` — {s['reason']}")
    return "\n".join(lines)
