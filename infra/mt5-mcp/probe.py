"""Exercise the real MCP stdio protocol, with either a fake or native terminal.

Run with the isolated reviewed runtime. Writes only sanitized profile outputs.
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4


def fake_child():
    from fxra.mt5_readonly import build_server

    class FakeService:
        def status(self):
            return {"connected": True, "environment": "fake", "execution": "unavailable"}

        def spec(self, symbol):
            return {"symbol": symbol}

        def quote(self, symbol):
            return {"symbol": symbol, "timestamp_status": "unverified"}

        def capture(self, symbol, count):
            if symbol != "EURUSD" or not 1 <= count <= 10000:
                raise ValueError("private-account-marker")
            return {"fake": True, "research_ready": False}

    build_server(FakeService()).run(transport="stdio")


async def probe(args):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from fxra.mt5_readonly import TOOLS

    repo = Path(__file__).resolve().parents[2]
    if args.fake:
        child_args = [str(Path(__file__).resolve()), "--fake-child"]
    else:
        child_args = ["-m", "fxra.mt5_readonly", "--terminal", args.terminal,
                      "--captures", str(Path(args.captures).resolve())]
    env = {"PYTHONPATH": str(repo / "src"), "PYTHONNOUSERSITE": "1"}
    for name in ("SystemRoot", "PATH", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        if name in os.environ:
            env[name] = os.environ[name]
    params = StdioServerParameters(command=sys.executable, args=child_args, env=env)
    results = {"mode": "fake" if args.fake else "native", "transport": "stdio"}
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=35)) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert names == TOOLS, "Unexpected MCP surface"
            results["tools"] = sorted(names)
            results["resources"] = len((await session.list_resources()).resources)
            assert results["resources"] == 0
            for mutation in ("order_send", "order_check", "place_order", "modify_order", "close_position",
                             "cancel_order", "login", "mt5_execute"):
                denied = await session.call_tool(mutation, {"approval_confirmed": True})
                assert denied.isError, "Unknown mutation was not refused"
            results["unknown_order_tool_rejected"] = True
            for name, payload in [("mt5_data_status", {}), ("mt5_symbol_spec", {"symbol": "EURUSD"}),
                                  ("mt5_quote", {"symbol": "EURUSD"}),
                                  ("mt5_capture_bars", {"symbol": "EURUSD", "count": args.count})]:
                response = await session.call_tool(name, payload)
                data = response.structuredContent
                if data is None and not response.isError:
                    text_parts = [part.text for part in response.content if part.type == "text"]
                    data = json.loads("\n".join(text_parts))
                results[name] = {"is_error": bool(response.isError),
                                 "data": data}
                if response.isError:
                    results[name]["error"] = "sanitized_tool_failure"
                    break
            bad = await session.call_tool("mt5_capture_bars", {"symbol": "EURUSD", "count": 10001})
            assert bad.isError
            # Ensure exception contents are not leaked through the MCP envelope.
            assert "private-account-marker" not in str(bad)
            results["invalid_capture_rejected"] = True
    dest = Path(args.evidence)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        archive = dest.with_name(f"{dest.stem}-{uuid4().hex[:12]}{dest.suffix}")
        archive.write_bytes(dest.read_bytes())
    dest.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    return int(any(value.get("is_error") for value in results.values() if isinstance(value, dict)))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--fake-child", action="store_true")
    p.add_argument("--fake", action="store_true")
    p.add_argument("--terminal", default=r"C:\Program Files\MetaTrader 5\terminal64.exe")
    p.add_argument("--captures", default="data/mt5-quarantine")
    p.add_argument("--count", type=int, default=1000)
    p.add_argument("--evidence", default="artifacts/mt5-mcp-review/protocol-native.json")
    a = p.parse_args()
    if a.fake_child:
        fake_child()
    else:
        raise SystemExit(asyncio.run(probe(a)))
