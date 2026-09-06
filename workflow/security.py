"""Security: permission scoping, destructive op guard, audit logging."""

from __future__ import annotations

import re
import getpass
from datetime import datetime, timezone
from typing import Any, Optional
import json
import threading

# ── Destructive operation patterns ──────────────────────────────────────────────

DESTRUCTIVE_PATTERNS = [
    re.compile(r"^\s*rm\s+-rf\s+"),
    re.compile(r"^\s*rmdir\s+"),
    re.compile(r"^\s*drop\s+table\s+", re.IGNORECASE),
    re.compile(r"^\s*drop\s+database\s+", re.IGNORECASE),
    re.compile(r"^\s*delete\s+from\s+", re.IGNORECASE),
    re.compile(r"^\s*truncate\s+", re.IGNORECASE),
    re.compile(r"^\s*kubectl\s+delete\s+", re.IGNORECASE),
    re.compile(r"^\s*docker\s+rm\s+", re.IGNORECASE),
    re.compile(r"^\s*kill\s+"),
    re.compile(r"--force"),
    re.compile(r"sudo\s+"),
]

NEED_CONFIRM_PATTERNS = [
    re.compile(r"^\s*git\s+push\s+.*--force", re.IGNORECASE),
    re.compile(r"^\s*kubectl\s+apply\s+.*--force", re.IGNORECASE),
]


class PermissionScope:
    """Represents the permission boundary for a workflow execution."""

    def __init__(
        self,
        user: Optional[str] = None,
        allowed_tools: Optional[list[str]] = None,
        blocked_tools: Optional[list[str]] = None,
        max_duration: int = 0,
        max_parallel_branches: int = 4,
    ):
        self.user = user or getpass.getuser()
        self.allowed_tools = set(allowed_tools) if allowed_tools else None
        self.blocked_tools = set(blocked_tools) if blocked_tools else set()
        self.max_duration = max_duration  # 0 = no limit
        self.max_parallel_branches = max_parallel_branches

    def can_run_tool(self, tool_name: str) -> bool:
        if tool_name in self.blocked_tools:
            return False
        if self.allowed_tools is None:
            return True
        return tool_name in self.allowed_tools

    def is_destructive(self, command: str) -> bool:
        return any(pat.search(command) for pat in DESTRUCTIVE_PATTERNS)

    def needs_confirmation(self, command: str) -> bool:
        return any(pat.search(command) for pat in NEED_CONFIRM_PATTERNS)


class AuditLogger:
    """Thread-safe audit log writer."""

    _INSERT_SQL = """
        INSERT INTO audit_log (id, execution_id, step_id, action, actor, details_json, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """

    def __init__(self, store: "ExecutionStore"):
        self.store = store
        self._lock = threading.Lock()

    def log(
        self,
        execution_id: str,
        action: str,
        step_id: Optional[str] = None,
        actor: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> None:
        import uuid
        with self._lock:
            self.store.db.execute(
                self._INSERT_SQL,
                (str(uuid.uuid4()), execution_id, step_id, action, actor or getpass.getuser(), json.dumps(details or {}, default=str), datetime.now(timezone.utc).isoformat()),
            )
            self.store.db.commit()
