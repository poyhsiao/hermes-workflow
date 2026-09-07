"""Tests for storage/templates: path traversal guard."""

import tempfile
from pathlib import Path

import pytest

from storage.templates import TemplateRegistry


@pytest.fixture
def registry(tmp_path):
    return TemplateRegistry(template_dir=tmp_path)


class TestSafeName:
    def test_valid_names(self, registry):
        assert registry._safe_name("my-workflow") == "my-workflow"
        assert registry._safe_name("workflow_v2") == "workflow_v2"
        assert registry._safe_name("a") == "a"
        assert registry._safe_name("Workflow123") == "Workflow123"

    def test_path_traversal_blocked(self, registry):
        for bad in ("../etc/passwd", "..\\windows\\system32", "../../", "../foo/bar"):
            with pytest.raises(ValueError, match="Invalid template name"):
                registry._safe_name(bad)

    def test_slash_blocked(self, registry):
        for bad in ("foo/bar", "foo\\bar", "/absolute", "trailing/"):
            with pytest.raises(ValueError, match="Invalid template name"):
                registry._safe_name(bad)

    def test_empty_blocked(self, registry):
        with pytest.raises(ValueError):
            registry._safe_name("")
        with pytest.raises(ValueError):
            registry._safe_name("   ")

    def test_leading_digit_rejected(self, registry):
        with pytest.raises(ValueError):
            registry._safe_name("123workflow")

    def test_special_chars_rejected(self, registry):
        for bad in ("wf@stage", "wf#1", "wf&name", "wf!test", "wf$var"):
            with pytest.raises(ValueError):
                registry._safe_name(bad)


class TestTemplateCrudPathTraversal:
    def test_save_rejects_path_traversal(self, registry):
        with pytest.raises(ValueError):
            registry.save("../../../etc/passwd", "name: x\nsteps: []")

    def test_save_rejects_absolute_path(self, registry):
        with pytest.raises(ValueError):
            registry.save("/etc/passwd", "name: x\nsteps: []")

    def test_load_rejects_path_traversal(self, registry):
        registry.save("legit", "name: x\nsteps: []")
        with pytest.raises(ValueError):
            registry.load("../../../etc/passwd")

    def test_delete_rejects_path_traversal(self, registry):
        registry.save("legit", "name: x\nsteps: []")
        with pytest.raises(ValueError):
            registry.delete("../../../etc/passwd")

    def test_roundtrip_valid_name(self, registry):
        yaml = "name: test\nsteps: []"
        path = registry.save("my-workflow-v1", yaml, description="test")
        loaded = registry.load("my-workflow-v1")
        assert loaded == yaml
        assert registry.delete("my-workflow-v1")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
