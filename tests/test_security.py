"""Tests for workflow security: PermissionScope and destructive-op guard."""

import os
import tempfile

import pytest

from storage.sqlite_store import ExecutionStore
from workflow.security import (
    AuditLogger,
    DESTRUCTIVE_PATTERNS,
    NEED_CONFIRM_PATTERNS,
    PermissionScope,
)


@pytest.fixture
def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return ExecutionStore(db_path=path)


class TestPermissionScope:
    def test_allowed_allows_any_tool(self):
        scope = PermissionScope()
        assert scope.can_run_tool("any_tool")
        assert scope.can_run_tool("another_tool")

    def test_allowed_tools_restricts(self):
        scope = PermissionScope(allowed_tools=["tool_a", "tool_b"])
        assert scope.can_run_tool("tool_a")
        assert scope.can_run_tool("tool_b")
        assert not scope.can_run_tool("tool_c")

    def test_blocked_tools_takes_precedence(self):
        scope = PermissionScope(allowed_tools=["tool_a", "tool_b"], blocked_tools=["tool_a"])
        assert not scope.can_run_tool("tool_a")  # blocked wins
        assert scope.can_run_tool("tool_b")

    def test_blocked_tools_only(self):
        scope = PermissionScope(blocked_tools=["dangerous_tool"])
        assert scope.can_run_tool("safe_tool")
        assert not scope.can_run_tool("dangerous_tool")

    def test_user_defaults_to_getpass(self):
        scope = PermissionScope()
        assert scope.user  # non-empty string

    def test_user_can_be_set(self):
        scope = PermissionScope(user="ci_runner")
        assert scope.user == "ci_runner"

    def test_max_duration_default_zero(self):
        scope = PermissionScope()
        assert scope.max_duration == 0

    def test_max_parallel_branches_default(self):
        scope = PermissionScope()
        assert scope.max_parallel_branches == 4


class TestDestructivePatternDetection:
    def _check(self, pattern, cmd):
        return bool(pattern.search(cmd))

    def test_rm_rf_detected(self):
        for cmd in ["rm -rf /", "rm -rf /tmp", "  rm -rf /var/log"]:
            assert self._check(DESTRUCTIVE_PATTERNS[0], cmd), f"Should detect: {cmd}"

    def test_rmdir_detected(self):
        for cmd in ["rmdir /tmp/dir", "  rmdir /data"]:
            assert self._check(DESTRUCTIVE_PATTERNS[1], cmd), f"Should detect: {cmd}"

    def test_drop_table_detected(self):
        assert self._check(DESTRUCTIVE_PATTERNS[2], "DROP TABLE users")

    def test_truncate_detected(self):
        assert self._check(DESTRUCTIVE_PATTERNS[3], "truncate logs")

    def test_kubectl_delete_detected(self):
        cmd = "kubectl delete pod my-pod"
        assert self._check(DESTRUCTIVE_PATTERNS[4], cmd)

    def test_docker_rm_detected(self):
        cmd = "docker rm my_container"
        assert self._check(DESTRUCTIVE_PATTERNS[5], cmd)

    def test_kill_detected(self):
        cmd = "kill -9 1234"
        assert self._check(DESTRUCTIVE_PATTERNS[6], cmd)

    def test_force_flag_detected(self):
        # --force anywhere in command (kubectl apply --force, docker run --force, etc.)
        cmd = "git push --force"
        assert self._check(DESTRUCTIVE_PATTERNS[8], cmd)

    def test_sudo_detected(self):
        cmd = "sudo rm -rf /"
        assert self._check(DESTRUCTIVE_PATTERNS[7], cmd)


class TestPermissionScopeIsDestructive:
    def test_echo_rm_is_destructive(self):
        # echo rm -rf is destructive: workflow security cannot trust echo
        # as a safe wrapper since context variable injection can bypass it
        scope = PermissionScope()
        assert scope.is_destructive("echo rm -rf /")
        assert scope.is_destructive("echo rmdir /tmp/dir")

    def test_actual_rm_is_destructive(self):
        scope = PermissionScope()
        assert scope.is_destructive("rm -rf /tmp")
        # rm without flags is not flagged as destructive
        assert not scope.is_destructive("rm /tmp/file")

    def test_rmdir_is_destructive(self):
        scope = PermissionScope()
        assert scope.is_destructive("rmdir /tmp/dir")


class TestNeedsConfirmation:
    def test_git_force_push_needs_confirm(self):
        scope = PermissionScope()
        assert scope.needs_confirmation("git push --force origin main")

    def test_kubectl_apply_force_needs_confirm(self):
        scope = PermissionScope()
        assert scope.needs_confirmation("kubectl apply -f deployment.yaml --force")


class TestCommandAllowlist:
    def test_python_not_in_allowlist(self):
        """python3/python should not be in the safe command allowlist (arbitrary code exec risk)."""
        scope = PermissionScope()
        assert not scope.is_command_allowed("python3 -c 'import os;os.system(\"id\")'")
        assert not scope.is_command_allowed("python -c 'import os;os.system(\"id\")'")

    def test_safe_commands_still_allowed(self):
        scope = PermissionScope()
        assert scope.is_command_allowed("ls /tmp")
        assert scope.is_command_allowed("grep pattern /tmp/file")
        assert scope.is_command_allowed("curl https://example.com")


class TestPermissionScopeFromDefinition:
    def test_empty_permission_returns_default_scope(self):
        scope = PermissionScope.from_workflow_definition(None)
        assert scope.allowed_tools is None
        assert scope.blocked_tools == set()

    def test_permission_dict_parsed(self):
        perm = {"allowed_tools": ["a", "b"], "blocked_tools": ["c"]}
        scope = PermissionScope.from_workflow_definition(perm)
        assert scope.allowed_tools == {"a", "b"}
        assert scope.blocked_tools == {"c"}

    def test_permission_empty_dict(self):
        scope = PermissionScope.from_workflow_definition({})
        assert scope.allowed_tools is None
        assert scope.blocked_tools == set()


class TestAuditLogger:
    def test_audit_log_writes_to_store(self, store):
        logger = AuditLogger(store)
        logger.log("exec-1", "workflow.start")
        logs = store.get_audit_log("exec-1")
        assert len(logs) == 1
        assert logs[0]["action"] == "workflow.start"

    def test_audit_log_with_step_id(self, store):
        logger = AuditLogger(store)
        logger.log("exec-2", "step.completed", step_id="step-1")
        logs = store.get_audit_log("exec-2")
        assert len(logs) == 1
        assert logs[0]["step_id"] == "step-1"

    def test_audit_log_with_actor(self, store):
        logger = AuditLogger(store)
        logger.log("exec-3", "workflow.start", actor="test-user")
        logs = store.get_audit_log("exec-3")
        assert len(logs) == 1
        assert logs[0]["actor"] == "test-user"

    def test_audit_log_with_details(self, store):
        logger = AuditLogger(store)
        logger.log("exec-4", "workflow.start", details={"foo": "bar", "count": 42})
        logs = store.get_audit_log("exec-4")
        assert len(logs) == 1
        assert logs[0]["action"] == "workflow.start"

    def test_audit_log_thread_safe(self, store):
        import threading

        logger = AuditLogger(store)
        threads = []
        exec_id = "exec-thread-safe"
        for i in range(5):

            def log_n(n=i, exec_id=exec_id):

                def inner():
                    for _ in range(10):
                        logger.log(exec_id, f"action.{n}")

                return inner

            t = threading.Thread(target=log_n(i))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        logs = store.get_audit_log(exec_id)
        # exec-thread-safe had 50 entries total (5 threads × 10 logs each)
        assert len(logs) == 50


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
