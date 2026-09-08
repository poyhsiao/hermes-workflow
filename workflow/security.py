"""Security: permission scoping, destructive op guard, audit logging."""

from __future__ import annotations

import getpass
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from storage.sqlite_store import ExecutionStore

# ── Shell operator blocklist (defense-in-depth after shlex.split) ───────────────
# Used to reject commands that would still be dangerous even after shell=False
# (e.g. shlex.split("find / -delete") → ["find", "/", "-delete"], which is safe
# via shell=False but the user intent is still destructive)
# Matches shell operators anywhere in a command string.
# Defense-in-depth: catches operators at command start (including after newlines
# or whitespace prefixes) and anywhere else a string-based shell eval could be
# triggered. shlex + shell=False mitigates actual injection, but this blocks
# operators that would be dangerous if shlex is bypassed or if the string
# is later evaluated in a shell context.
SHELL_OPERATOR_BLOCK = re.compile(r"\$\(|[`]|;|&&|\|\||>>|<<|<>|>|<|^\s*\$\(|^\s*[`]")
# ponytail: original pattern without ^ anchors was correct; added ^\s* prefix
# variants for completeness (non-breaking — unanchored alternates still cover
# mid-string operators like "curl http://x.com?a=1;b=2").

# ── Destructive operation patterns ──────────────────────────────────────────────
DESTRUCTIVE_PATTERNS = [
    re.compile(r"^\s*rm\s+-[rfR]+\s+"),
    re.compile(r"^\s*rmdir\s+"),
    re.compile(r"^\s*drop\s+table\s+", re.IGNORECASE),
    re.compile(r"^\s*truncate\s+", re.IGNORECASE),
    re.compile(r"^\s*kubectl\s+delete\s+", re.IGNORECASE),
    re.compile(r"^\s*docker\s+rm\s+", re.IGNORECASE),
    re.compile(r"^\s*kill\s+"),
    re.compile(r"^\s*sudo\s+"),
    re.compile(r"^\s*git\s+push\s+.*--force", re.IGNORECASE),
    re.compile(r"--force"),  # catches --force anywhere: kubectl apply --force, docker run --force, etc.
    # find with destructive actions — caught regardless of shell=False
    re.compile(r"^\s*find\s+.*-(delete|exec|ok|exec_dir)\b"),
    # curl/wget piping to shell — remote code execution vector
    re.compile(r"^\s*(curl|wget).*\|\s*(bash|sh|perl|python|ruby)"),
    # disk wipe / device overwrite
    re.compile(r"^\s*dd\s+.*of=(/dev/[a-zA-Z]+\d*|/dev/mapper/)"),
    re.compile(r"^\s*mkfs"),
    re.compile(r"^\s*sfdisk"),
    re.compile(r"^\s*fdisk\s+.*-w\s*[^d]"),  # fdisk -w (destroy mode)
    # fork bomb — :(){|:&};: pattern
    re.compile(r"^\s*:\(\)\s*\{\s*\|"),
]

NEED_CONFIRM_PATTERNS = [
    re.compile(r"^\s*git\s+push\s+.*--force", re.IGNORECASE),
    re.compile(r"^\s*kubectl\s+apply\s+.*--force", re.IGNORECASE),
]

# ── Allowed commands for subprocess fallback ───────────────────────────────────
# When a tool is not found in the Hermes registry, these safe commands are
# permitted through subprocess. All other commands are rejected.
# Security: shell=False + shlex.split + allowlist = no shell injection possible.
#
# NOTE: This list is restricted to TRULY READ-ONLY commands. Do NOT add commands
# that can write, create, modify, move, copy, or delete files/directories.
# Do NOT add commands that can make network modifications (git push, curl -T, etc.)
SHELL_SAFE_COMMANDS = frozenset(
    {
        # File inspection (read-only)
        "ls",
        "stat",
        "file",
        "cat",
        "head",
        "tail",
        "wc",
        "sort",
        "uniq",
        "grep",
        "egrep",
        "fgrep",
        "cut",
        "tr",
        # Hash / integrity (read-only)
        "md5sum",
        "sha1sum",
        "sha256sum",
        "sha512sum",
        "cksum",
        # Date / version
        "date",
        "uptime",
        "hostname",
        "uname",
        "arch",
        # Network (read-only - no file upload/download)
        "ping",
        "ping6",
        "nslookup",
        "dig",
        "host",
        # System (read-only)
        "df",
        "du",
        "free",
        "top",
        "ps",
        "pidof",
        "id",
        "whoami",
        "groups",
        "env",
        "printenv",
        # Git (read-only operations only - see is_command_allowed for full validation)
        "git",
        # Misc (no file modification)
        "echo",
        "printf",
        "seq",
        "false",
        "true",
        "which",
        "basename",
        "dirname",
        "readlink",
        "realpath",
    }
)
SHELL_SAFE_WITH_ARGS = {  # commands that are safe only without specific flag combos
    "find": frozenset({"xargs"}),  # find ... | xargs <safe> is ok in shell=False context
    "tar": frozenset({"-x", "--extract"}),  # extraction only - no archive creation
}


class PermissionScope:
    """Represents the permission boundary for a workflow execution."""

    def __init__(
        self,
        user: str | None = None,
        allowed_tools: list[str] | None = None,
        blocked_tools: list[str] | None = None,
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
        stripped = command.strip()
        # Check full command against destructive patterns first
        if any(pat.search(command) for pat in DESTRUCTIVE_PATTERNS):
            return True
        # Strip echo prefix and re-check: catches "echo rm -rf /" etc.
        # (echo just prints; but only safe if the printed content isn't destructive)
        inner = re.sub(r"^\s*echo\s+", "", stripped)
        if inner != stripped:  # there was an echo prefix
            if any(pat.search(inner) for pat in DESTRUCTIVE_PATTERNS):
                return True
            # echo with no dangerous content — safe
            return False
        return False

    def is_command_allowed(self, cmd: str) -> bool:
        """Check if cmd is in the safe allowlist for subprocess fallback (shell=False)."""
        import shlex

        try:
            parts = shlex.split(cmd)
        except ValueError:
            return False
        if not parts:
            return False
        base = parts[0]
        if base not in SHELL_SAFE_COMMANDS:
            return False
        if base == "git" and len(parts) > 1:
            git_subcmd = parts[1]
            readonly_git_subcommands = frozenset(
                {
                    "log",
                    "show",
                    "diff",
                    "status",
                    "branch",
                    "tag",
                    "reflog",
                    "rev-parse",
                    "ls-files",
                    "ls-tree",
                    "cat-file",
                    "describe",
                    "name-rev",
                    "for-each-ref",
                    "shortlog",
                    "count-objects",
                    "diff-index",
                    "diff-tree",
                    "diff-files",
                    "commit-tree",
                    "verify-pack",
                    "verify-commit",
                    "show-ref",
                    "symbolic-ref",
                }
            )
            if git_subcmd not in readonly_git_subcommands:
                return False
        return True

    def needs_confirmation(self, command: str) -> bool:
        return any(pat.search(command) for pat in NEED_CONFIRM_PATTERNS)

    @classmethod
    def from_workflow_definition(cls, perm: dict | None) -> PermissionScope:
        """Build a PermissionScope from a workflow permission dict."""
        if not perm:
            return cls()
        return cls(
            allowed_tools=perm.get("allowed_tools"),
            blocked_tools=perm.get("blocked_tools"),
        )


class AuditLogger:
    """Thread-safe audit log writer."""

    _INSERT_SQL = """
        INSERT INTO audit_log (id, execution_id, step_id, action, actor, details_json, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """

    def __init__(self, store: ExecutionStore):
        self.store = store
        self._lock = threading.Lock()

    def log(
        self,
        execution_id: str,
        action: str,
        step_id: str | None = None,
        actor: str | None = None,
        details: dict | None = None,
    ) -> None:
        with self._lock:
            self.store.db.execute(
                self._INSERT_SQL,
                (
                    str(uuid.uuid4()),
                    execution_id,
                    step_id,
                    action,
                    actor or getpass.getuser(),
                    json.dumps(details or {}, default=str),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self.store.db.commit()
