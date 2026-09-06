"""Version control for workflow definitions — rollback to prior versions."""
from __future__ import annotations

import difflib
from typing import Optional

from workflow.core import WorkflowDefinition
from storage.sqlite_store import ExecutionStore


class VersionedStore:
    """Manages workflow definition versions and rollback to prior versions."""

    def __init__(self, store: ExecutionStore):
        self.store = store

    def save(self, defn: WorkflowDefinition, changed_by: Optional[str] = None, change_summary: str = "") -> None:
        """Save a new version of an existing workflow."""
        existing = self.store.db.execute(
            "SELECT id FROM workflow_definitions WHERE name=?", (defn.name,)
        ).fetchone()
        if existing:
            self.store.update_definition(existing["id"], defn, changed_by, change_summary)
        else:
            self.store.save_definition(defn, changed_by)

    def get(self, name: str, version: Optional[int] = None) -> Optional[WorkflowDefinition]:
        """Get a specific version, or latest if version is None."""
        if version is not None:
            existing = self.store.db.execute(
                "SELECT id FROM workflow_definitions WHERE name=?", (name,)
            ).fetchone()
            if not existing:
                return None
            return self.store.get_version(existing["id"], version)
        return self.store.get_definition(name)

    def list_versions(self, name: str) -> list[dict]:
        """List all versions of a named workflow."""
        existing = self.store.db.execute(
            "SELECT id FROM workflow_definitions WHERE name=?", (name,)
        ).fetchone()
        if not existing:
            return []
        return self.store.list_versions(existing["id"])

    def rollback_definition(self, name: str, to_version: int, changed_by: Optional[str] = None) -> Optional[WorkflowDefinition]:
        """Rollback a workflow definition to a prior version."""
        old_defn = self.get(name, to_version)
        if not old_defn:
            return None
        old_defn.version = self._current_version(name)
        self.save(old_defn, changed_by, f"rollback to version {to_version}")
        return old_defn

    def diff(self, name: str, v1: int, v2: int) -> dict:
        """Return line-by-line diff between two versions of a workflow."""
        def1 = self.get(name, v1)
        def2 = self.get(name, v2)
        if not def1 or not def2:
            return {"ok": False, "error": f"Version not found for '{name}'"}

        yaml1 = def1.definition_yaml or ""
        yaml2 = def2.definition_yaml or ""

        unified = list(difflib.unified_diff(
            yaml1.splitlines(keepends=True),
            yaml2.splitlines(keepends=True),
            fromfile=f"v{v1}",
            tofile=f"v{v2}",
            lineterm="",
        ))
        return {
            "ok": True,
            "name": name,
            "v1": v1,
            "v2": v2,
            "diff": "".join(unified) or f"No textual differences between v{v1} and v{v2}.",
            "steps_v1": len(def1.steps),
            "steps_v2": len(def2.steps),
        }

    def _current_version(self, name: str) -> int:
        row = self.store.db.execute(
            "SELECT version FROM workflow_definitions WHERE name=?", (name,)
        ).fetchone()
        return row["version"] if row else 0
