"""Verosight search tools: search_posts, list_posts, list_comments."""
from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.schemas.errors import ErrorReport
from app.tools.base import ToolResult
from app.tools.registry import register_tool
from app.tools.verosight.client import VerosightClient
from app.tools.verosight.helpers import summarize_search_posts_response, summarize_verosight_response


class VerosightSearchPostsInput(BaseModel):
    query: str = Field(..., description="Semantic search query string")
    limit: int = Field(default=10, ge=1, le=100, description="Max number of posts to return")
    cursor: str | None = Field(default=None, description="Pagination cursor")


class VerosightListPostsInput(BaseModel):
    limit: int = Field(default=10, ge=1, le=100, description="Max number of posts to return")
    cursor: str | None = Field(default=None, description="Pagination cursor")
    author: str | None = Field(default=None, description="Filter posts by author username")


class VerosightListCommentsInput(BaseModel):
    post_id: str | None = Field(default=None, description="Filter comments for a specific post ID")
    limit: int = Field(default=10, ge=1, le=100, description="Max number of comments to return")
    cursor: str | None = Field(default=None, description="Pagination cursor")


@register_tool(
    name="verosight_search_posts",
    description="Perform semantic search across posts on Verosight. Use when finding posts matching a query or topic.",
    source="verosight",
    cost_credits=5,
    tags=["verosight", "search"],
    parameters=VerosightSearchPostsInput.model_json_schema(),
)
async def verosight_search_posts(**kwargs: Any) -> ToolResult:
    start_time = time.perf_counter()

    try:
        validated = VerosightSearchPostsInput(**kwargs)
    except ValidationError as ve:
        report = ErrorReport(
            component="verosight",
            stage="validation",
            message=f"Validation failed for verosight_search_posts: {ve}",
            endpoint="/v1/search",
            retryable=False,
        )
        return ToolResult(
            tool_name="verosight_search_posts",
            status="error",
            error=report.model_dump_json(),
            duration_ms=int((time.perf_counter() - start_time) * 1000),
        )

    client = VerosightClient()
    res = await client.request(
        method="POST",
        endpoint="/v1/search",
        json_data=validated.model_dump(),
    )

    elapsed = int((time.perf_counter() - start_time) * 1000)
    if res.error is not None:
        return ToolResult(
            tool_name="verosight_search_posts",
            status="error",
            error=res.error.model_dump_json(),
            duration_ms=elapsed,
        )

    summarized = summarize_search_posts_response(res.data, query=validated.query, max_posts=5)
    credits_used = res.credits_used if res.credits_used > 0 else 5
    return ToolResult(
        tool_name="verosight_search_posts",
        status="success",
        data=summarized,
        raw_data=res.full_envelope,
        duration_ms=elapsed,
        credits_used=credits_used,
    )


@register_tool(
    name="verosight_list_posts",
    description="Retrieve recent posts on Verosight. Use when browsing or reading recent posts without a specific search query.",
    source="verosight",
    cost_credits=2,
    tags=["verosight", "posts"],
    parameters=VerosightListPostsInput.model_json_schema(),
)
async def verosight_list_posts(**kwargs: Any) -> ToolResult:
    start_time = time.perf_counter()

    try:
        validated = VerosightListPostsInput(**kwargs)
    except ValidationError as ve:
        report = ErrorReport(
            component="verosight",
            stage="validation",
            message=f"Validation failed for verosight_list_posts: {ve}",
            endpoint="/v1/posts",
            retryable=False,
        )
        return ToolResult(
            tool_name="verosight_list_posts",
            status="error",
            error=report.model_dump_json(),
            duration_ms=int((time.perf_counter() - start_time) * 1000),
        )

    client = VerosightClient()
    res = await client.request(
        method="GET",
        endpoint="/v1/posts",
        params=validated.model_dump(),
    )

    elapsed = int((time.perf_counter() - start_time) * 1000)
    if res.error is not None:
        return ToolResult(
            tool_name="verosight_list_posts",
            status="error",
            error=res.error.model_dump_json(),
            duration_ms=elapsed,
        )

    summarized = summarize_verosight_response(res.data)
    credits_used = res.credits_used if res.credits_used > 0 else 2
    return ToolResult(
        tool_name="verosight_list_posts",
        status="success",
        data=summarized,
        raw_data=res.full_envelope,
        duration_ms=elapsed,
        credits_used=credits_used,
    )


@register_tool(
    name="verosight_list_comments",
    description="List comments on Verosight. Use when retrieving comments for a post or recent user comments.",
    source="verosight",
    cost_credits=2,
    tags=["verosight", "comments"],
    parameters=VerosightListCommentsInput.model_json_schema(),
)
async def verosight_list_comments(**kwargs: Any) -> ToolResult:
    start_time = time.perf_counter()

    try:
        validated = VerosightListCommentsInput(**kwargs)
    except ValidationError as ve:
        report = ErrorReport(
            component="verosight",
            stage="validation",
            message=f"Validation failed for verosight_list_comments: {ve}",
            endpoint="/v1/comments",
            retryable=False,
        )
        return ToolResult(
            tool_name="verosight_list_comments",
            status="error",
            error=report.model_dump_json(),
            duration_ms=int((time.perf_counter() - start_time) * 1000),
        )

    client = VerosightClient()
    res = await client.request(
        method="GET",
        endpoint="/v1/comments",
        params=validated.model_dump(),
    )

    elapsed = int((time.perf_counter() - start_time) * 1000)
    if res.error is not None:
        return ToolResult(
            tool_name="verosight_list_comments",
            status="error",
            error=res.error.model_dump_json(),
            duration_ms=elapsed,
        )

    summarized = summarize_verosight_response(res.data)
    credits_used = res.credits_used if res.credits_used > 0 else 2
    return ToolResult(
        tool_name="verosight_list_comments",
        status="success",
        data=summarized,
        raw_data=res.full_envelope,
        duration_ms=elapsed,
        credits_used=credits_used,
    )
