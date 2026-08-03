"""
scripts/dump_mcp_tools.py

Cetak setiap tool MCP Verosight PERSIS seperti yang akan diterima model lewat
OpenAI Responses API. Outputnya adalah hasil `registry.to_responses_tools()`
untuk tool ber-source "mcp" — pipeline yang sama dipakai runtime — sehingga apa
yang tercetak di sini identik dengan payload `tools` yang dikirim ke model.

Jalankan dari root repo:
    python -m scripts.dump_mcp_tools
    python -m scripts.dump_mcp_tools --show-schema
    python -m scripts.dump_mcp_tools --alias verosight --show-schema

Prasyarat .env: VEROSIGHT_API_KEY terisi, VEROSIGHT_MODE=mcp (atau both).
Skrip ini MEMANGGIL server Verosight yang asli — tanpa mock, sesuai aturan 1.6.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.config import get_settings
from app.mcp.adapter import MCPTool, mcp_tool_to_spec
from app.mcp.client import MCPClient
from app.tools.registry import ToolRegistry


async def _run(alias: str, show_schema: bool, compare: bool) -> int:
    settings = get_settings()

    if settings.VEROSIGHT_API_KEY in ("", "vlt_live_xxx"):
        print(
            "ERROR: VEROSIGHT_API_KEY belum diisi di .env. Skrip ini memanggil "
            "server asli, tidak ada mock.",
            file=sys.stderr,
        )
        return 2

    if settings.VEROSIGHT_MODE == "rest":
        print(
            "PERINGATAN: VEROSIGHT_MODE=rest → registry akan memfilter keluar "
            "semua tool MCP. Set VEROSIGHT_MODE=mcp (atau both) dulu.",
            file=sys.stderr,
        )

    # ------------------------------------------------------------------ #
    # Sambungkan ke Verosight MCP.
    # CATATAN: sesuaikan konstruktor ini bila signature MCPClient Anda beda.
    # Yang dibutuhkan skrip: .connect(), .list_tools(), .call_tool(), .close().
    # ------------------------------------------------------------------ #
    client = MCPClient(
        alias="verosight",
        url=settings.VEROSIGHT_MCP_SSE_URL,
        headers={"X-API-Key": settings.VEROSIGHT_API_KEY},
    )
    await client.connect()

    try:
        listed = await client.list_tools()
        # SDK mcp mengembalikan ListToolsResult(.tools); wrapper Anda mungkin
        # sudah membuka jadi list. Tangani keduanya.
        tools = getattr(listed, "tools", listed)

        registry = ToolRegistry()
        originals: dict[str, str] = {}
        for tool in tools:
            spec = mcp_tool_to_spec(tool, server_alias=alias, available=True)
            originals[spec.name] = (tool.description or tool.name)
            registry.register(MCPTool(client, tool.name, spec), overwrite=True)

        # Inilah payload yang benar-benar dikirim ke Responses API:
        exposed = registry.to_responses_tools()

        print()
        print(f"Server   : {settings.VEROSIGHT_MCP_SSE_URL}")
        print(f"Alias    : {alias}")
        print(f"MODE     : {settings.VEROSIGHT_MODE}")
        print(
            f"Tool MCP : {len(tools)} diterima dari server, "
            f"{len(exposed)} diekspos ke model"
        )
        print("=" * 72)

        mismatches = 0
        for t in exposed:
            print(f"\n> {t['name']}")
            print(f"  description: {t['description']}")

            if compare:
                orig = originals.get(t["name"], "")
                verbatim = t["description"] == orig.strip()
                if not verbatim:
                    mismatches += 1
                print(f"  verbatim?  : {'YA' if verbatim else 'TIDAK - lihat di bawah'}")
                if not verbatim:
                    print(f"  (verosight): {orig!r}")

            if show_schema:
                schema = json.dumps(t["parameters"], indent=2, ensure_ascii=False)
                print("  parameters :")
                for line in schema.splitlines():
                    print(f"    {line}")

        print("\n" + "=" * 72)
        print(
            "Yang tercetak di atas = persis payload `tools` yang dikirim ke "
            "Responses API saat runtime."
        )
        if compare:
            if mismatches == 0:
                print("Semua deskripsi verbatim dari Verosight (hanya nama diberi prefiks).")
            else:
                print(f"ADA {mismatches} deskripsi yang TIDAK verbatim - periksa adapter.")

        return 0
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dump tool MCP Verosight seperti yang dilihat OpenAI Responses API."
    )
    parser.add_argument(
        "--alias",
        default="verosight",
        help="prefiks nama tool (default: verosight)",
    )
    parser.add_argument(
        "--show-schema",
        action="store_true",
        help="cetak juga JSON Schema `parameters` tiap tool",
    )
    parser.add_argument(
        "--no-compare",
        action="store_true",
        help="lewati pengecekan verbatim deskripsi vs Verosight",
    )
    args = parser.parse_args()
    rc = asyncio.run(_run(args.alias, args.show_schema, not args.no_compare))
    sys.exit(rc)


if __name__ == "__main__":
    main()