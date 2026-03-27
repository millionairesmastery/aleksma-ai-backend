"""
Database connection pool using psycopg2.

Provides init_db / close_db (FastAPI lifespan) and get_connection (per-request).
"""

from __future__ import annotations

import os

import psycopg2
from psycopg2 import pool

_pool: pool.SimpleConnectionPool | None = None


def init_db():
    global _pool
    url = os.environ.get("DATABASE_URL", "postgresql://localhost:5432/ai_cad_studio")
    _pool = pool.SimpleConnectionPool(minconn=1, maxconn=10, dsn=url)


def get_connection():
    if _pool is None:
        raise RuntimeError("Database pool not initialised — call init_db() first")
    return _pool.getconn()


def put_connection(conn):
    if _pool is not None:
        _pool.putconn(conn)


def close_db():
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
