"""Verosight analytics tools: sentiment, sentiment_trend, trending, topics."""
from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.schemas.errors import ErrorReport
from app.tools.base import ToolResult
from app.tools.registry import register_tool
from app.tools.verosight.client import VerosightClient
from app.tools.verosight.helpers import summarize_verosight_response


class VerosightSentimentInput(BaseModel):
    query: str = Field(..., description="Query string to calculate overall sentiment score for (required)")
    timeframe: str | None = Field(default="7d", description="Timeframe window, e.g. '24h', '7d', '30d'")


class VerosightSentimentTrendInput(BaseModel):
    query: str | None = Field(default=None, description="Query topic to track sentiment trend for")
    timeframe: str | None = Field(default="30d", description="Total timeframe window, e.g. '7d', '30d'")
    interval: str | None = Field(default="1d", description="Data point interval, e.g. '1h', '1d', '1w'")


class VerosightTrendingInput(BaseModel):
    category: str | None = Field(default=None, description="Filter trending topics by category")
    limit: int = Field(default=10, ge=1, le=50, description="Max number of trending items to return")


class VerosightTopicsInput(BaseModel):
    query: str | None = Field(default=None, description="Query string to cluster discussion topics for")
    limit: int = Field(default=10, ge=1, le=50, description="Max number of topic clusters to return")


@register_tool(
    name="verosight_sentiment",
    description="Analyze overall sentiment for a specific query topic on Verosight. Query parameter is REQUIRED. Use when evaluating aggregate sentiment.",
    source="verosight",
    cost_credits=5,
    tags=["verosight", "analytics", "sentiment"],
    parameters=VerosightSentimentInput.model_json_schema(),
)
async def verosight_sentiment(**kwargs: Any) -> ToolResult:
    start_time = time.perf_counter()

    try:
        validated = VerosightSentimentInput(**kwargs)
    except ValidationError as ve:
        report = ErrorReport(
            component="verosight",
            stage="validation",
            message=f"Validation failed for verosight_sentiment: {ve}",
            endpoint="/v1/analytics/sentiment",
            retryable=False,
        )
        return ToolResult(
            tool_name="verosight_sentiment",
            status="error",
            error=report.model_dump_json(),
            duration_ms=int((time.perf_counter() - start_time) * 1000),
        )

    client = VerosightClient()
    res = await client.request(
        method="GET",
        endpoint="/v1/analytics/sentiment",
        params=validated.model_dump(),
    )

    elapsed = int((time.perf_counter() - start_time) * 1000)
    if res.error is not None:
        return ToolResult(
            tool_name="verosight_sentiment",
            status="error",
            error=res.error.model_dump_json(),
            duration_ms=elapsed,
        )

    summarized = summarize_verosight_response(res.data)
    credits_used = res.credits_used if res.credits_used > 0 else 5
    return ToolResult(
        tool_name="verosight_sentiment",
        status="success",
        data=summarized,
        raw_data=res.full_envelope,
        duration_ms=elapsed,
        credits_used=credits_used,
    )


@register_tool(
    name="verosight_sentiment_trend",
    description="Track sentiment trends over time on Verosight. Use when monitoring historical sentiment progression.",
    source="verosight",
    cost_credits=3,
    tags=["verosight", "analytics", "sentiment"],
    parameters=VerosightSentimentTrendInput.model_json_schema(),
)
async def verosight_sentiment_trend(**kwargs: Any) -> ToolResult:
    start_time = time.perf_counter()

    try:
        validated = VerosightSentimentTrendInput(**kwargs)
    except ValidationError as ve:
        report = ErrorReport(
            component="verosight",
            stage="validation",
            message=f"Validation failed for verosight_sentiment_trend: {ve}",
            endpoint="/v1/analytics/sentiment-trend",
            retryable=False,
        )
        return ToolResult(
            tool_name="verosight_sentiment_trend",
            status="error",
            error=report.model_dump_json(),
            duration_ms=int((time.perf_counter() - start_time) * 1000),
        )

    client = VerosightClient()
    res = await client.request(
        method="GET",
        endpoint="/v1/analytics/sentiment-trend",
        params=validated.model_dump(),
    )

    elapsed = int((time.perf_counter() - start_time) * 1000)
    if res.error is not None:
        return ToolResult(
            tool_name="verosight_sentiment_trend",
            status="error",
            error=res.error.model_dump_json(),
            duration_ms=elapsed,
        )

    summarized = summarize_verosight_response(res.data)
    credits_used = res.credits_used if res.credits_used > 0 else 3
    return ToolResult(
        tool_name="verosight_sentiment_trend",
        status="success",
        data=summarized,
        raw_data=res.full_envelope,
        duration_ms=elapsed,
        credits_used=credits_used,
    )


@register_tool(
    name="verosight_trending",
    description="Retrieve currently trending topics or posts on Verosight. Use when discovering viral or popular subjects.",
    source="verosight",
    cost_credits=5,
    tags=["verosight", "analytics", "trending"],
    parameters=VerosightTrendingInput.model_json_schema(),
)
async def verosight_trending(**kwargs: Any) -> ToolResult:
    start_time = time.perf_counter()

    try:
        validated = VerosightTrendingInput(**kwargs)
    except ValidationError as ve:
        report = ErrorReport(
            component="verosight",
            stage="validation",
            message=f"Validation failed for verosight_trending: {ve}",
            endpoint="/v1/analytics/trending",
            retryable=False,
        )
        return ToolResult(
            tool_name="verosight_trending",
            status="error",
            error=report.model_dump_json(),
            duration_ms=int((time.perf_counter() - start_time) * 1000),
        )

    client = VerosightClient()
    res = await client.request(
        method="GET",
        endpoint="/v1/analytics/trending",
        params=validated.model_dump(),
    )

    elapsed = int((time.perf_counter() - start_time) * 1000)
    if res.error is not None:
        return ToolResult(
            tool_name="verosight_trending",
            status="error",
            error=res.error.model_dump_json(),
            duration_ms=elapsed,
        )

    summarized = summarize_verosight_response(res.data)
    credits_used = res.credits_used if res.credits_used > 0 else 5
    return ToolResult(
        tool_name="verosight_trending",
        status="success",
        data=summarized,
        raw_data=res.full_envelope,
        duration_ms=elapsed,
        credits_used=credits_used,
    )


@register_tool(
    name="verosight_topics",
    description="Discover and cluster discussion topics on Verosight. Use when analyzing themes or recurring subjects.",
    source="verosight",
    cost_credits=5,
    tags=["verosight", "analytics", "topics"],
    parameters=VerosightTopicsInput.model_json_schema(),
)
async def verosight_topics(**kwargs: Any) -> ToolResult:
    start_time = time.perf_counter()

    try:
        validated = VerosightTopicsInput(**kwargs)
    except ValidationError as ve:
        report = ErrorReport(
            component="verosight",
            stage="validation",
            message=f"Validation failed for verosight_topics: {ve}",
            endpoint="/v1/analytics/topics",
            retryable=False,
        )
        return ToolResult(
            tool_name="verosight_topics",
            status="error",
            error=report.model_dump_json(),
            duration_ms=int((time.perf_counter() - start_time) * 1000),
        )

    client = VerosightClient()
    res = await client.request(
        method="GET",
        endpoint="/v1/analytics/topics",
        params=validated.model_dump(),
    )

    elapsed = int((time.perf_counter() - start_time) * 1000)
    if res.error is not None:
        return ToolResult(
            tool_name="verosight_topics",
            status="error",
            error=res.error.model_dump_json(),
            duration_ms=elapsed,
        )

    summarized = summarize_verosight_response(res.data)
    credits_used = res.credits_used if res.credits_used > 0 else 5
    return ToolResult(
        tool_name="verosight_topics",
        status="success",
        data=summarized,
        raw_data=res.full_envelope,
        duration_ms=elapsed,
        credits_used=credits_used,
    )
