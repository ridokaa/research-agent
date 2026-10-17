import re
from typing import Any


def clean_html(text: str) -> str:
    """Remove HTML tags, script/div blocks, and normalize whitespace."""
    if not isinstance(text, str):
        return ""
    # Strip HTML tags & inline scripts/styles
    clean = re.sub(r"<(script|style|div)[^>]*>.*?</\1>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r"<[^>]+>", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def summarize_search_posts_response(
    data: Any,
    query: str | None = None,
    max_posts: int = 5,
) -> dict[str, Any]:
    """Summarize Verosight search/list posts response for LLM context.

    1. Places aggregates (total_matched, returned_in_context, note, query, model) at the TOP of the dictionary.
    2. Keeps essential fields for each post: type, platform, profile_name, posted_at, similarity, content.
    3. Guarantees posted_at is always explicitly included (uses 'n/a' if missing).
    4. Truncates content to ~5000 characters per post.
    5. Limits list of posts in context to max_posts (default 5) while preserving full total_matched.
    """
    if not isinstance(data, dict):
        return {
            "total_matched": 0,
            "returned_in_context": 0,
            "note": "menampilkan 0 dari 0 hasil teratas",
            "query": query,
            "model": "semantic",
            "results": [],
        }

    raw_results = data.get("results", [])
    if not isinstance(raw_results, list):
        raw_results = []

    total_matched = data.get("total", len(raw_results))
    model = data.get("model", "semantic")

    summarized_posts: list[dict[str, Any]] = []
    for item in raw_results[:max_posts]:
        if not isinstance(item, dict):
            continue

        raw_content = item.get("content", "")
        if isinstance(raw_content, str):
            continue
            # content = clean_html(raw_content)
            # if len(content) > 5000:
            #     content = content[:5000] + "..."
        else:
            content = ""

        posted_at_val = item.get("posted_at")
        posted_at_str = str(posted_at_val).strip() if posted_at_val else "n/a"

        post_summary: dict[str, Any] = {
            "type": item.get("type", "post"),
            "platform": item.get("platform", "unknown"),
            "profile_name": item.get("profile_name", "unknown"),
            "posted_at": posted_at_str,
        }
        if "similarity" in item and item["similarity"] is not None:
            post_summary["similarity"] = item["similarity"]
        post_summary["content"] = content

        summarized_posts.append(post_summary)

    # Return dictionary with AGGREGATES FIRST
    returned_cnt = len(summarized_posts)
    out: dict[str, Any] = {
        "total_matched": total_matched,
        "returned_in_context": returned_cnt,
        "note": f"menampilkan {returned_cnt} dari {total_matched} hasil teratas",
    }
    if query is not None:
        out["query"] = query
    out["model"] = model
    out["results"] = summarized_posts
    return out


def summarize_verosight_response(obj: Any) -> Any:
    """Summarize Verosight response payload for LLM consumption.

    If TOOL_RESULT_PASSTHROUGH is True, returns obj untouched.
    """
    try:
        from app.config import get_settings
        if get_settings().TOOL_RESULT_PASSTHROUGH:
            return obj
    except Exception:
        pass

    if isinstance(obj, dict):
        if "results" in obj and "total" in obj:
            return summarize_search_posts_response(obj)

        result: dict[str, Any] = {}
        for k, v in obj.items():
            # Skip empty media_urls
            if k == "media_urls" and (
                v is None or (isinstance(v, (list, str, dict)) and len(v) == 0)
            ):
                continue

            # Truncate sample_* lists to max 5 items
            if isinstance(k, str) and k.startswith("sample_") and isinstance(v, list):
                result[k] = [summarize_verosight_response(item) for item in v[:5]]
            else:
                result[k] = summarize_verosight_response(v)
        return result

    if isinstance(obj, list):
        return [summarize_verosight_response(item) for item in obj]

    return obj
