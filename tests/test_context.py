"""Tests for WorkflowContext: resolve_var injection escaping, resolve_args, rollback."""

import pytest

from workflow.context import WorkflowContext


class TestWorkflowContextBasic:
    def test_set_and_get(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        ctx.set("name", "Alice")
        assert ctx.get("name") == "Alice"

    def test_get_default(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        assert ctx.get("missing") is None
        assert ctx.get("missing", "default") == "default"

    def test_push_and_pop(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        ctx.push("first")
        ctx.push("second")
        assert ctx.pop() == "second"
        assert ctx.pop() == "first"
        assert ctx.pop() is None

    def test_last_output(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        assert ctx.last_output() is None
        ctx.push("value")
        assert ctx.last_output() == "value"

    def test_emit_event(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        ctx.emit_event("step.started", {"step": "a"})
        assert len(ctx.events) == 1
        assert ctx.events[0]["name"] == "step.started"
        assert ctx.events[0]["payload"] == {"step": "a"}

    def test_checkpoints_list(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        ctx.set("x", 1)
        cp = ctx.checkpoint(step_index=0)
        assert cp["step_index"] == 0
        assert cp["shared"] == {"x": 1}
        assert len(ctx.checkpoints) == 1


class TestResolveVarInjectionEscaping:
    def test_resolve_var_substitutes_value(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        ctx.set("name", "Alice")
        result = ctx.resolve_var("Hello {{ name }}")
        assert result == "Hello Alice"

    def test_resolve_var_escapes_dollar(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        ctx.set("path", "$HOME")
        result = ctx.resolve_var("Path: {{ path }}")
        assert "$" in result  # $ is escaped
        assert "\\$" in result or result == "Path: \\$HOME"

    def test_resolve_var_escapes_backtick(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        ctx.set("cmd", "`ls`")
        result = ctx.resolve_var("Cmd: {{ cmd }}")
        assert "\\`" in result

    def test_resolve_var_escapes_semicolon(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        ctx.set("val", "a;b")
        result = ctx.resolve_var("Val: {{ val }}")
        assert "\\;" in result

    def test_resolve_var_escapes_ampersand(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        ctx.set("val", "a&b")
        result = ctx.resolve_var("Val: {{ val }}")
        assert "\\&" in result

    def test_resolve_var_escapes_pipe(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        ctx.set("val", "a|b")
        result = ctx.resolve_var("Val: {{ val }}")
        assert "\\|" in result

    def test_resolve_var_escapes_angle_brackets(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        ctx.set("val", "<script>")
        result = ctx.resolve_var("Val: {{ val }}")
        assert "\\<" in result
        assert "\\>" in result

    def test_resolve_var_escapes_double_quotes(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        ctx.set("val", 'say "hello"')
        result = ctx.resolve_var("Val: {{ val }}")
        assert '\\"' in result

    def test_resolve_var_escapes_single_quotes(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        ctx.set("val", "it's")
        result = ctx.resolve_var("Val: {{ val }}")
        assert "\\'" in result

    def test_resolve_var_escapes_newline(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        ctx.set("val", "line1\nline2")
        result = ctx.resolve_var("Val: {{ val }}")
        # Newline becomes literal \n (backslash + letter n)
        assert "Val: line1\\nline2" == result

    def test_resolve_var_escapes_backslash_correctly(self):
        """Backslash in value should become \\\\ so subsequent escapes don't double-escape."""
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        ctx.set("val", r"path\to\file")  # literal backslashes
        result = ctx.resolve_var("Path: {{ val }}")
        # Each backslash should become \\\\
        assert result == r"Path: path\\to\\file"

    def test_resolve_var_backslash_dollar_escaped_correctly(self):
        """Backslash+dollar value (backslash+dollar+HOME) gets single-pass escaped.
        shlex.split treats \\\\$ as literal — no variable expansion."""
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        ctx.set("val", "\\$HOME")  # \$ = \ + $ + HOME = 2 backslashes + $ + HOME
        result = ctx.resolve_var("Cmd: {{ val }}")
        # Single-pass: \ -> \\\\, $ -> \$, giving 5 backslash chars before $
        assert result == "Cmd: " + "\\\\\\" + "$HOME"

    def test_resolve_var_multiple_identical_placeholders(self):
        """Multiple identical {{ var }} placeholders should all be replaced."""
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        ctx.set("x", "value")
        result = ctx.resolve_var("{{ x }} {{ x }}")
        assert result == "value value"

    def test_resolve_var_non_string_unchanged(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        ctx.set("num", 42)
        assert ctx.resolve_var(42) == 42
        assert ctx.resolve_var(None) is None


class TestResolveArgs:
    def test_resolve_args_simple(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        ctx.set("name", "Bob")
        args = {"msg": "Hello {{ name }}"}
        resolved = ctx.resolve_args(args)
        assert "{{ name }}" not in resolved["msg"]
        assert "Bob" in resolved["msg"]

    def test_resolve_args_nested(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        ctx.set("key", "secret")
        args = {"outer": {"inner": "value: {{ key }}"}}
        resolved = ctx.resolve_args(args)
        assert "{{ key }}" not in resolved["outer"]["inner"]
        assert "secret" in resolved["outer"]["inner"]

    def test_resolve_args_list(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        ctx.set("item", "ITEM")
        args = {"items": ["{{ item }}", "static"]}
        resolved = ctx.resolve_args(args)
        assert resolved["items"][0] != "{{ item }}"
        assert resolved["items"][1] == "static"

    def test_resolve_args_mixed_types(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        ctx.set("val", "X")
        args = {"str": "{{ val }}", "num": 42, "bool": True, "none": None}
        resolved = ctx.resolve_args(args)
        assert "{{ val }}" not in resolved["str"]
        assert resolved["num"] == 42
        assert resolved["bool"] is True
        assert resolved["none"] is None


class TestRollbackTo:
    def test_rollback_restores_state(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        ctx.set("x", 1)
        ctx.push("a")
        cp = ctx.checkpoint(step_index=1)
        ctx.set("x", 999)
        ctx.push("b")
        ctx.push("c")
        ctx.rollback_to(cp)
        assert ctx.shared == {"x": 1}
        assert ctx.pipeline == ["a"]

    def test_rollback_trims_checkpoints(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        cp1 = ctx.checkpoint(step_index=0)
        ctx.set("x", 1)
        ctx.checkpoint(step_index=1)
        ctx.set("x", 2)
        assert len(ctx.checkpoints) == 2
        ctx.rollback_to(cp1)
        assert len(ctx.checkpoints) == 1

    def test_rollback_handles_missing_keys(self):
        ctx = WorkflowContext(workflow_id="w1", execution_id="e1")
        cp = {"step_index": 0, "shared": {}, "pipeline": [], "events": []}
        ctx.rollback_to(cp)  # should not raise
        assert ctx.shared == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
