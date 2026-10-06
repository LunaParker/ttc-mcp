"""Helpers shared by the live integration modules."""
import time

from fastmcp import Client


async def call(url: str, name: str, **args) -> dict:
    async with Client(url) as c:
        t = time.perf_counter()
        r = (await c.call_tool(name, args)).data
    r["_ms"] = round((time.perf_counter() - t) * 1000)
    assert "error" not in r, (name, args, r.get("error"))
    return r


def ids(elements, key="alert_id") -> set:
    return {e.get(key) for e in elements}
