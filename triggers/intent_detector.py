"""Intent detection hook: pre_llm_call + on_message to suggest workflows.

Embedding: nomic-embed-text-v1.5 via ONNX (local, no external API).
Scoring: cosine similarity + keyword fallback.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any

import numpy as np

from storage.sqlite_store import ExecutionStore

logger = logging.getLogger(__name__)

# Keyword → workflow name hints (exact match, case-insensitive)
KEYWORD_HINTS = {
    "ci": "ci-check",
    "test": "run-tests",
    "deploy": "deploy",
    "build": "build",
    "release": "release",
    "backup": "backup-db",
    "migrate": "migrate-db",
    "lint": "lint",
    "format": "format-code",
    "audit": "security-audit",
    "sync": "sync-data",
    "report": "generate-report",
    "review": "code-review",
    "scan": "security-scan",
    "restore": "restore-db",
    "benchmark": "run-benchmark",
    "onboard": "onboard",
    "setup": "setup-project",
}

# ponytail: singleton ONNX session guarded by lock
_onnx_session: Any = None
_onnx_lock = threading.Lock()

# ponytail: singleton ExecutionStore guarded by lock
_store: Any = None
_store_lock = threading.Lock()

# Module-level workflow embedding cache (name -> embedding vector)
_workflow_embeddings: dict[str, list[float]] = {}
_workflow_emb_lock = threading.Lock()

# ponytail: tokenizer instance for BERT-based nomic model
_tokenizer: Any = None
_tokenizer_lock = threading.Lock()


def _get_store() -> Any:
    """Lazily initialize and return the shared ExecutionStore."""
    global _store
    if _store is not None:
        return _store
    with _store_lock:
        if _store is not None:
            return _store
        _store = ExecutionStore()
    return _store


def _get_tokenizer() -> Any:
    """Lazily load BERT tokenizer for nomic-embed-text-v1.5."""
    global _tokenizer
    if _tokenizer is not None:
        return _tokenizer
    with _tokenizer_lock:
        if _tokenizer is not None:
            return _tokenizer
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained("bert-base-uncased")
        _tokenizer = tokenizer
    return _tokenizer


def _get_onnx_session() -> Any:
    """Lazily load ONNX session for nomic-embed-text-v1.5."""
    global _onnx_session
    if _onnx_session is not None:
        return _onnx_session
    with _onnx_lock:
        # Double-check after acquiring lock
        if _onnx_session is not None:
            return _onnx_session
        from onnxruntime import InferenceSession

        model_path = os.environ.get(
            "HERMES_INTENT_MODEL_PATH",
            os.path.join(os.path.dirname(__file__), "..", "models", "nomic-embed-text-v1.5.onnx"),
        )
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Intent model not found at {model_path}")
        _onnx_session = InferenceSession(model_path, providers=["CPUExecutionProvider"])
    return _onnx_session


def _tokenize_for_model(text: str, max_tokens: int) -> dict:
    """Tokenize text using BERT tokenizer for ONNX model input.

    Returns dict with input_ids, attention_mask, and token_type_ids.
    """
    tokenizer = _get_tokenizer()
    encoded = tokenizer(
        text,
        max_length=max_tokens,
        padding="max_length",
        truncation=True,
        return_tensors="np",
    )
    return {
        "input_ids": encoded["input_ids"],
        "attention_mask": encoded["attention_mask"],
        "token_type_ids": encoded.get("token_type_ids", np.zeros_like(encoded["input_ids"])),
    }


def _embed_text(text: str) -> list[float]:
    """Embed text using local ONNX nomic-embed-text-v1.5. Returns normalized float vector."""
    sess = _get_onnx_session()
    max_tokens = int(os.environ.get("HERMES_INTENT_MAX_TOKENS", "512"))
    inputs = _tokenize_for_model(text, max_tokens)

    input_bindings = {}
    for input_meta in sess.get_inputs():
        name = input_meta.name
        if name == "input_ids":
            input_bindings[name] = inputs["input_ids"].astype(np.int64)
        elif name == "attention_mask":
            input_bindings[name] = inputs["attention_mask"].astype(np.int64)
        elif name == "token_type_ids":
            input_bindings[name] = inputs["token_type_ids"].astype(np.int64)
        else:
            input_bindings[name] = inputs.get(name, inputs["input_ids"].astype(np.int64))

    output_names = [out.name for out in sess.get_outputs()]
    outputs = sess.run(output_names, input_bindings)

    if len(outputs) == 1:
        vec = outputs[0]
        if vec.ndim == 3:
            vec = _mean_pool_tokens(vec, inputs["attention_mask"])
        elif vec.ndim == 2:
            pass
        vec = vec[0] if vec.shape[0] == 1 else vec
    else:
        last_idx = len(output_names) - 1
        if "last_hidden_state" in output_names:
            last_idx = output_names.index("last_hidden_state")
        vec = outputs[last_idx]
        if vec.ndim == 3:
            vec = _mean_pool_tokens(vec, inputs["attention_mask"])

    norm = np.linalg.norm(vec)
    vec = vec / norm if norm > 0 else vec
    return vec.flatten().tolist()


def _mean_pool_tokens(token_vectors: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    """Mean-pool valid token vectors, ignoring padding."""
    mask_expanded = np.expand_dims(attention_mask, axis=-1)
    mask_expanded = np.where(mask_expanded == 0, 1e-9, mask_expanded)
    summed = np.sum(token_vectors * mask_expanded, axis=1)
    counts = np.sum(mask_expanded, axis=1)
    pooled = summed / counts
    return pooled


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Dot product of normalized vectors = cosine similarity."""
    return sum(x * y for x, y in zip(a, b, strict=True))


def _workflow_text(workflow_def: dict) -> str:
    """Build search text from a workflow definition dict (list_definitions format)."""
    # list_definitions() returns dicts with definition_yaml; parse steps/tags from it
    # ponytail: avoids extra DB round-trips; parsing cost negligible vs embedding
    definition_yaml = workflow_def.get("definition_yaml", "")
    tags: list[str] = []
    step_names: list[str] = []
    if definition_yaml:
        try:
            import yaml
            parsed = yaml.safe_load(definition_yaml) or {}
            tags = parsed.get("tags", []) or []
            step_names = [s.get("name", "") for s in parsed.get("steps", []) if s.get("name")]
        except Exception:
            pass  # yaml parse failure — degrade gracefully
    parts = [
        workflow_def.get("name", ""),
        workflow_def.get("description", ""),
        " ".join(tags),
        " ".join(step_names),
    ]
    return " ".join(p for p in parts if p)


def detect_workflow_intent(messages: list[dict]) -> list[dict]:
    """Analyze recent messages for workflow trigger patterns.

    Scoring: cosine similarity of user message embedding vs each workflow's
    pre-computed text embedding. Keyword matches override low semantic scores.

    Args:
        messages: List of conversation message dicts with "content" field.

    Returns:
        List of dicts sorted by score (highest first):
        {
            "workflow": str,       # workflow name
            "score": float,        # cosine similarity in [0, 1]
            "reason": str,         # e.g. "semantic match (0.73)" or "keyword 'ci' matched"
            "suggest": str,        # e.g. "/workflow run ci-check"
            "matched_on": str,     # "semantic" | "keyword"
        }
    """
    if not messages:
        return []

    # Build combined message text
    recent = messages[-3:]
    combined_text = " ".join(
        m.get("content", "") if isinstance(m, dict) else str(m) for m in recent
    ).lower()

    # --- Keyword fallback (strong signal, bypasses semantic) ---
    keyword_matches: dict[str, dict] = {}
    for keyword, workflow_name in KEYWORD_HINTS.items():
        # Use word-boundary regex to avoid substring false positives (e.g. "contest" matching "test")
        if re.search(r"\b" + re.escape(keyword) + r"\b", combined_text, re.IGNORECASE):
            keyword_matches[workflow_name] = {
                "workflow": workflow_name,
                "score": 1.0,  # treated as high confidence
                "reason": f"keyword '{keyword}' matched",
                "suggest": f"/workflow run {workflow_name}",
                "matched_on": "keyword",
            }

    # --- Semantic similarity ---
    try:
        user_vec = _embed_text(combined_text)
    except Exception as exc:
        # ONNX not available or model missing — fall back to keyword-only
        logger.warning("intent_detector: user embedding failed, falling back to keywords: %s", exc)
        return list(keyword_matches.values())

    # Load all workflow definitions using shared store
    all_defs = _get_store().list_definitions()

    # Use module-level embedding cache for cross-call efficiency
    semantic_results: list[dict] = []

    for defn in all_defs:
        name = defn.get("name", "")
        if name in keyword_matches:
            # Already matched by keyword — skip semantic to save compute
            continue

        if name not in _workflow_embeddings:
            text = _workflow_text(defn)
            try:
                with _workflow_emb_lock:
                    # Double-check after acquiring lock
                    if name not in _workflow_embeddings:
                        _workflow_embeddings[name] = _embed_text(text)
            except Exception as exc:
                logger.warning("intent_detector: failed to embed workflow %r: %s", name, exc)
                continue

        score = _cosine_similarity(user_vec, _workflow_embeddings[name])

        semantic_results.append(
            {
                "workflow": name,
                "score": round(score, 4),
                "reason": f"semantic match ({score:.2f})",
                "suggest": f"/workflow run {name}",
                "matched_on": "semantic",
            }
        )

    # Sort semantic by score descending
    semantic_results.sort(key=lambda x: x["score"], reverse=True)
    raw_top_k = os.environ.get("HERMES_INTENT_TOP_K", "5")
    try:
        top_k = max(1, int(raw_top_k))  # ensure positive
    except ValueError:
        top_k = 5
    top_semantic = semantic_results[:top_k]

    # Merge: keyword matches always included, then top semantic results
    keyword_vals = list(keyword_matches.values())
    merged = keyword_vals + [r for r in top_semantic if r["workflow"] not in keyword_matches]

    # Re-sort merged list by score descending so keyword+semantic ordering is consistent
    merged.sort(key=lambda x: x["score"], reverse=True)
    return merged


def make_suggestion_message(suggestions: list[dict]) -> str | None:
    """Format suggestions into a user-facing prompt."""
    if not suggestions:
        return None
    lines = ["**Workflow suggestions:**"]
    for s in suggestions[:3]:
        badge = "🔑" if s["matched_on"] == "keyword" else "🧠"
        lines.append(f"- {badge} `{s['suggest']}` — {s['reason']} (score={s['score']:.2f})")
    return "\n".join(lines)
