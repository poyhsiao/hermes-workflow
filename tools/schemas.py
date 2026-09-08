"""OpenAI function-call schemas for all workflow tools."""

SCHEMAS: dict[str, dict] = {
    "workflow_run": {
        "type": "function",
        "function": {
            "name": "workflow_run",
            "description": "Start a named workflow execution with optional context overrides. Returns an execution ID immediately — the workflow runs asynchronously.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the workflow definition to execute."},
                    "context_overrides": {
                        "type": "object",
                        "description": 'Override values for the workflow\'s context schema (e.g. {"pr_number": "123"}).',
                    },
                },
                "required": ["name"],
            },
        },
    },
    "workflow_stop": {
        "type": "function",
        "function": {
            "name": "workflow_stop",
            "description": "Stop a running workflow execution immediately.",
            "parameters": {
                "type": "object",
                "properties": {
                    "execution_id": {"type": "string", "description": "The execution ID returned by workflow_run."},
                },
                "required": ["execution_id"],
            },
        },
    },
    "workflow_status": {
        "type": "function",
        "function": {
            "name": "workflow_status",
            "description": "Get the current status and step progress of a running or completed workflow execution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "execution_id": {"type": "string", "description": "The execution ID returned by workflow_run."},
                },
                "required": ["execution_id"],
            },
        },
    },
    "workflow_define": {
        "type": "function",
        "function": {
            "name": "workflow_define",
            "description": "Define or update a workflow from a YAML string. The workflow is validated before saving.",
            "parameters": {
                "type": "object",
                "properties": {
                    "yaml": {"type": "string", "description": "YAML workflow definition string."},
                },
                "required": ["yaml"],
            },
        },
    },
    "workflow_delete": {
        "type": "function",
        "function": {
            "name": "workflow_delete",
            "description": "Delete a workflow definition and all its execution history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the workflow to delete."},
                },
                "required": ["name"],
            },
        },
    },
    "workflow_list": {
        "type": "function",
        "function": {
            "name": "workflow_list",
            "description": "List all registered workflow definitions with their current versions.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "workflow_show": {
        "type": "function",
        "function": {
            "name": "workflow_show",
            "description": "Show the YAML source of a workflow definition.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the workflow to show."},
                },
                "required": ["name"],
            },
        },
    },
    "workflow_history": {
        "type": "function",
        "function": {
            "name": "workflow_history",
            "description": "Get the execution history for a workflow.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_name": {"type": "string", "description": "Name of the workflow."},
                    "limit": {"type": "integer", "description": "Maximum number of executions to return (default 50)."},
                },
                "required": ["workflow_name"],
            },
        },
    },
    "workflow_rollback": {
        "type": "function",
        "function": {
            "name": "workflow_rollback",
            "description": "Rollback a failed or stopped execution to its last checkpoint, or to a specific definition version.",
            "parameters": {
                "type": "object",
                "properties": {
                    "execution_id": {"type": "string", "description": "The execution ID to rollback."},
                    "to_version": {
                        "type": "integer",
                        "description": "Optional: rollback to a specific workflow definition version and re-run from there.",
                    },
                },
                "required": ["execution_id"],
            },
        },
    },
    "workflow_export": {
        "type": "function",
        "function": {
            "name": "workflow_export",
            "description": "Export a workflow definition as YAML (or JSON with --json flag).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the workflow to export."},
                    "format": {
                        "type": "string",
                        "enum": ["yaml", "json"],
                        "description": "Export format (default: yaml).",
                    },
                },
                "required": ["name"],
            },
        },
    },
    "workflow_import": {
        "type": "function",
        "function": {
            "name": "workflow_import",
            "description": "Import a workflow from a YAML string. Validates before saving.",
            "parameters": {
                "type": "object",
                "properties": {
                    "yaml": {"type": "string", "description": "YAML workflow definition string."},
                },
                "required": ["yaml"],
            },
        },
    },
    "workflow_diff": {
        "type": "function",
        "function": {
            "name": "workflow_diff",
            "description": "Compare two versions of a workflow definition and show what changed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the workflow."},
                    "v1": {"type": "integer", "description": "First version number."},
                    "v2": {"type": "integer", "description": "Second version number."},
                },
                "required": ["name", "v1", "v2"],
            },
        },
    },
    "workflow_metrics": {
        "type": "function",
        "function": {
            "name": "workflow_metrics",
            "description": "Get execution metrics for a workflow: pass/fail rates, average duration, step timings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the workflow."},
                    "period": {"type": "string", "description": "Time period to analyze (e.g. '7d', '30d')."},
                },
                "required": ["name"],
            },
        },
    },
    "workflow_suggest": {
        "type": "function",
        "function": {
            "name": "workflow_suggest",
            "description": "Suggest relevant workflows based on the current conversation context. Analyzes recent messages to infer intent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Maximum number of suggestions to return (default 3)."},
                },
            },
        },
    },
    "workflow_template_save": {
        "type": "function",
        "function": {
            "name": "workflow_template_save",
            "description": "Save a workflow definition as a reusable named template.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Template name."},
                    "yaml": {"type": "string", "description": "YAML workflow definition content."},
                },
                "required": ["name", "yaml"],
            },
        },
    },
    "workflow_template_list": {
        "type": "function",
        "function": {
            "name": "workflow_template_list",
            "description": "List all saved workflow templates.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "workflow_template_load": {
        "type": "function",
        "function": {
            "name": "workflow_template_load",
            "description": "Load a saved template's YAML content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Template name to load."},
                },
                "required": ["name"],
            },
        },
    },
    "workflow_template_delete": {
        "type": "function",
        "function": {
            "name": "workflow_template_delete",
            "description": "Delete a saved workflow template.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Template name to delete."},
                },
                "required": ["name"],
            },
        },
    },
}
