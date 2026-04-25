"""Shared ClickHouse utility helpers for RAG diagnostic scripts.

Centralises client / store creation so every script uses
the same settings and avoids code duplication.
"""
from __future__ import annotations

import clickhouse_connect
from clickhouse_connect.driver.client import Client

from rag_chat import settings, _make_embeddings
from clickhouse_store import ClickHouseVectorStore, ClickHouseStoreSettings
from langchain_ollama import OllamaEmbeddings


def _make_ch_settings() -> ClickHouseStoreSettings:
    return ClickHouseStoreSettings(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_username,
        password=settings.clickhouse_password,
        database=settings.clickhouse_database,
        table=settings.clickhouse_table,
    )


def get_client() -> Client:
    """Create a clickhouse-connect Client from settings."""
    cfg = _make_ch_settings()
    return clickhouse_connect.get_client(
        host=cfg.host,
        port=cfg.port,
        username=cfg.username,
        password=cfg.password,
    )


def get_store(embedding: OllamaEmbeddings | None = None) -> ClickHouseVectorStore:
    """Return a ready-to-use ClickHouseVectorStore connected via settings.

    Args:
        embedding: Optional pre-created embeddings; a new one is created if omitted.
    """
    cfg = _make_ch_settings()
    client = get_client()
    emb = embedding or _make_embeddings()
    return ClickHouseVectorStore(client=client, embedding=emb, cfg=cfg)


def make_embeddings() -> OllamaEmbeddings:
    """Return the configured OllamaEmbeddings instance (delegated to rag_chat)."""
    return _make_embeddings()
