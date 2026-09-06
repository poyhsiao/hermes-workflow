"""Template registry: save, list, load community-curated workflow templates."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

DEFAULT_TEMPLATE_DIR = Path.home() / ".hermes" / "workflow_templates"


class TemplateRegistry:
    """Manages workflow templates stored on disk."""

    def __init__(self, template_dir: Path | None = None):
        self.template_dir = (template_dir or DEFAULT_TEMPLATE_DIR)
        self.template_dir.mkdir(parents=True, exist_ok=True)

    def save(self, name: str, yaml_content: str, description: str = "", tags: list[str] | None = None) -> str:
        """Save a workflow as a template file."""
        # Prevent path traversal
        safe_name = name.replace("..", "").replace("/", "_").replace("\\", "_").strip()
        if not safe_name:
            raise ValueError(f"Invalid template name: {name!r}")
        path = self.template_dir / f"{safe_name}.yaml"
        meta_path = self.template_dir / f"{safe_name}.meta.json"
        path.write_text(yaml_content)
        meta = {"name": name, "description": description, "tags": tags or [], "saved_at": str(path.stat().st_mtime)}
        meta_path.write_text(json.dumps(meta, indent=2))
        return str(path)

    def list(self) -> list[dict]:
        """List all available templates."""
        templates = []
        for yaml_path in self.template_dir.glob("*.yaml"):
            name = yaml_path.stem
            meta_path = self.template_dir / f"{name}.meta.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text())
            else:
                meta = {"name": name, "description": "", "tags": []}
            templates.append(meta)
        return templates

    def _safe_name(self, name: str) -> str:
        """Normalize name to prevent path traversal."""
        safe = name.replace("..", "").replace("/", "_").replace("\\", "_").strip()
        if not safe:
            raise ValueError(f"Invalid template name: {name!r}")
        return safe

    def load(self, name: str) -> str | None:
        """Load template YAML content by name."""
        path = self.template_dir / f"{self._safe_name(name)}.yaml"
        if not path.exists():
            return None
        return path.read_text()

    def delete(self, name: str) -> bool:
        """Delete a template by name."""
        safe = self._safe_name(name)
        yaml_path = self.template_dir / f"{safe}.yaml"
        meta_path = self.template_dir / f"{safe}.meta.json"
        removed = False
        if yaml_path.exists():
            yaml_path.unlink()
            removed = True
        if meta_path.exists():
            meta_path.unlink()
        return removed

    def export_all(self, target_dir: Path) -> int:
        """Export all templates to a target directory."""
        target_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for yaml_path in self.template_dir.glob("*.yaml"):
            shutil.copy2(yaml_path, target_dir / yaml_path.name)
            name = yaml_path.stem
            meta_path = self.template_dir / f"{name}.meta.json"
            if meta_path.exists():
                shutil.copy2(meta_path, target_dir / meta_path.name)
            count += 1
        return count

    def import_from_file(self, file_path: str, name: str | None = None) -> str:
        """Import a template from a YAML file."""
        src = Path(file_path)
        template_name = name or src.stem
        yaml_content = src.read_text()
        self.save(template_name, yaml_content)
        return template_name
