"""db_query tool implementation for safe read-only SQL queries."""
from __future__ import annotations

import asyncio
import re
from typing import Any

from sqlalchemy import text

from app.db.session import get_session
from app.tools.registry import register_tool

_FORBIDDEN_KEYWORDS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "CREATE",
    "ALTER",
    "TRUNCATE",
    "GRANT",
    "REVOKE",
    "EXEC",
    "EXECUTE",
    "VACUUM",
    "REINDEX",
    "COPY",
}

_ALLOWED_TABLES = {
    "conversations",
    "messages",
    "runs",
    "run_steps",
    "tool_invocations",
    "execution_memory",
}


def _validate_sql_query(raw_query: str) -> str:
    cleaned = raw_query.strip()
    if not cleaned:
        raise ValueError("SQL query cannot be empty.")

    stripped_semicolon = cleaned.rstrip(";")
    if ";" in stripped_semicolon:
        raise ValueError("Multi-statement SQL queries separated by ';' are forbidden.")

    query_upper = cleaned.upper()

    tokens = set(re.findall(r"\b[A-Z_]+\b", query_upper))
    forbidden_used = tokens.intersection(_FORBIDDEN_KEYWORDS)
    if forbidden_used:
        raise ValueError(
            f"DDL/DML operations are forbidden. Detected keywords: {', '.join(sorted(forbidden_used))}"
        )

    if not (query_upper.startswith("SELECT") or query_upper.startswith("WITH")):
        raise ValueError("Only read-only SELECT queries are allowed.")

    # Extract CTE names if present (e.g., WITH cte1 AS (...), cte2 AS (...))
    cte_names = set()
    if query_upper.startswith("WITH"):
        cte_matches = re.findall(r"\b([a-zA-Z0-9_]+)\s+AS\s*\(", cleaned, flags=re.IGNORECASE)
        cte_names = {c.lower() for c in cte_matches}

    allowed_targets = _ALLOWED_TABLES.union(cte_names)

    table_matches = re.findall(
        r"\b(?:FROM|JOIN)\s+([a-zA-Z0-9_]+)\b",
        cleaned,
        flags=re.IGNORECASE,
    )
    for tbl in table_matches:
        tbl_lower = tbl.lower()
        if tbl_lower not in allowed_targets:
            raise ValueError(
                f"Access to table '{tbl}' is forbidden. Whitelisted tables: {', '.join(sorted(_ALLOWED_TABLES))}"
            )

    if "LIMIT" not in query_upper:
        cleaned = f"SELECT * FROM ({stripped_semicolon}) AS _subquery LIMIT 100"
    else:
        match = re.search(r"\bLIMIT\s+(\d+)", query_upper)
        if match:
            limit_val = int(match.group(1))
            if limit_val > 100:
                cleaned = re.sub(
                    r"\bLIMIT\s+\d+", "LIMIT 100", cleaned, flags=re.IGNORECASE
                )

    return cleaned


@register_tool(
    name="db_query",
    description=(
        "Execute a read-only SELECT SQL query on rag_db database. "
        "Allowed tables: conversations, messages, runs, run_steps, tool_invocations, execution_memory. "
        "DDL/DML operations are forbidden and maximum 100 rows returned."
    ),
    source="local",
    cost_credits=0,
    timeout_s=5.0,
    tags=["db", "query"],
)
async def db_query(query: str) -> list[dict[str, Any]]:
    """Execute a safe, read-only SELECT query against the database."""
    safe_query = _validate_sql_query(query)

    async def _execute():
        async for session in get_session():
            try:
                await session.execute(text("SET LOCAL statement_timeout = '5000ms'"))
            except Exception:
                pass
            res = await session.execute(text(safe_query))
            mappings = res.mappings().all()
            return [dict(row) for row in mappings]
        return []

    return await asyncio.wait_for(_execute(), timeout=5.0)
