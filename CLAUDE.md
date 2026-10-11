# ttc-mcp

MCP server for the TTC network. `docs/spec.md` is normative: when code and spec disagree, fix one and say which. The README's "Gotchas the code encodes" lists the live data facts the code depends on; spec section 11 is the dated verification log.

## Commands

- `uv sync` (Python 3.14 from `.python-version`). `uv run pytest` runs the unit suite in `tests/unit` on the fixture store built from `tests/fixtures/gtfs-mini.zip`.
- `uv run pytest tests/integration` boots the real server and reaches the TTC's hosts; it is deliberately not in CI.
- `uv run ruff check .` is the lint CI runs. The formatter is not adopted; the dense style is deliberate.
- `uv run ttc-mcp-build` builds the static store into `~/.cache/ttc-mcp` (CKAN download, about 30 s per feed). Live smoke scripts live in `scripts/`.
- PyCharm run configurations: `Tests`, `Integration (live)`, `Server (stdio)`, `Build static`.

## How work lands

- Test first: a failing test, then the change. One commit per behaviour, plain subject line, the why in the body, no attribution lines.
- Branch, pull request, merge when CI is green. Pushes and pull requests happen on the maintainer's say-so.
- Comments at most two lines; the why belongs in the commit body. Pull request descriptions: at most one paragraph and five bullets.
- Spec edits ride with the change that needs them; a live finding gets a dated line in section 11.

## Shape of the code

- `adapters/` (one module per source, typed records plus a `SourceStatus`) feed `cache.py` (TTL, single-flight, conditional GET, stale-if-error, backoff); `domain/` is pure functions over the store and advisory snapshots; `tools/` are the FastMCP tools. Tools never touch HTTP directly.
- The static store is one SQLite file per feed under `manifest.json`; two feeds are loaded during a board-period overlap and `StaticStore.lookup_trip` tries both.
- Disruption semantics (closure spans, served end platforms, suspended platforms, shuttle legs) live in `domain/disruptions.py` and `domain/planner.py` and are specified in spec 6.7. Change them there, never in a tool.
- The unit fixture carries routes 1, 5, 121, 300, 400 and 504 only; Line 2 is absent, so its platforms carry no routes in tests.
