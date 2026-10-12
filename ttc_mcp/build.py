from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ttc_mcp.adapters.ckan import make_client
from ttc_mcp.config import settings
from ttc_mcp.static.bucket import GcsBlobs, pull, push
from ttc_mcp.static.refresh import build_from_zips, ensure_static


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr)


def main(argv=None) -> int:
    s = settings()
    p = argparse.ArgumentParser(prog="ttc-mcp-build",
                                description="Check the City of Toronto GTFS packages and rebuild the static store on change.")
    p.add_argument("--cache", type=Path, default=s.cache_dir)
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-surface", action="store_true", help="skip the surface feed (no crosswalk)")
    p.add_argument("--from-zip", type=Path, help="build from a local merged GTFS zip instead of CKAN")
    p.add_argument("--surface-zip", type=Path)
    p.add_argument("--no-ttcapi", action="store_true", help="skip the ttc.ca service-level fetch")
    p.add_argument("--no-checks", action="store_true", help="skip the post-build checks (spec 8 items 3 and 9)")
    p.add_argument("--bucket", default=s.static_bucket, help="pull this bucket's store before the CKAN check and push the build into it after (TTC_MCP_STATIC_BUCKET)")
    a = p.parse_args(argv)
    blobs = GcsBlobs(a.bucket) if a.bucket else None
    if a.from_zip:
        rep = build_from_zips(a.cache, a.from_zip, a.surface_zip, ttcapi=not a.no_ttcapi, checks=not a.no_checks)
    else:
        # A Job starts with an empty disk: the bucket's manifest is the state the ETags and the overlap pruning must see.
        pulled = pull(a.cache, blobs, log=_stderr) if blobs else None
        with make_client(s.user_agent) as client:
            rep = ensure_static(a.cache, client, force=a.force, surface=not a.no_surface, ttcapi=not a.no_ttcapi, checks=not a.no_checks)
        if blobs:
            rep["pulled"] = [f.feed_version for f in pulled.feeds] if pulled else []
    if blobs:
        rep["pushed"] = push(a.cache, blobs, log=_stderr)
    print(json.dumps({k: v for k, v in rep.items() if k != "manifest"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
