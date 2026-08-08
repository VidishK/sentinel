from __future__ import annotations

import json
from typing import Any

import boto3

from sentinel.config import get_settings


def _runtime():
    settings = get_settings()
    return boto3.client("bedrock-runtime", region_name=settings.aws_region)


def embed(text: str) -> list[float]:
    """Embed text with Titan Text Embeddings V2. Returns embed_dim floats."""
    settings = get_settings()
    body = {
        "inputText": text,
        "dimensions": settings.embed_dim,
        "normalize": True,
    }
    response = _runtime().invoke_model(
        modelId=settings.bedrock_embed_model_id,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(body),
    )
    payload = json.loads(response["body"].read())
    vector = payload["embedding"]
    if len(vector) != settings.embed_dim:
        raise RuntimeError(
            f"expected {settings.embed_dim}-dim embedding, got {len(vector)}"
        )
    return vector


def chat(
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    max_tokens: int = 2048,
    temperature: float = 0.0,
) -> str:
    """One-shot Claude call via Bedrock Converse-compatible InvokeModel."""
    settings = get_settings()
    body: dict[str, Any] = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
    }
    if system:
        body["system"] = system

    response = _runtime().invoke_model(
        modelId=settings.bedrock_model_id,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(body),
    )
    payload = json.loads(response["body"].read())
    parts = payload.get("content") or []
    texts = [p["text"] for p in parts if p.get("type") == "text"]
    return "".join(texts)


def vector_literal(values: list[float]) -> str:
    """Format a Python list as a CockroachDB VECTOR literal."""
    return "[" + ",".join(f"{v:.8f}" for v in values) + "]"
