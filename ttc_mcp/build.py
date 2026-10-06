from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ttc_mcp.adapters.ckan import make_client
from ttc_mcp.config import settings
from ttc_mcp.static.refresh import build_from_zips, ensure_static


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
    a = p.parse_args(argv)
    if a.from_zip:
        rep = build_from_zips(a.cache, a.from_zip, a.surface_zip, ttcapi=not a.no_ttcapi)
    else:
        with make_client(s.user_agent) as client:
            rep = ensure_static(a.cache, client, force=a.force, surface=not a.no_surface, ttcapi=not a.no_ttcapi)
    print(json.dumps({k: v for k, v in rep.items() if k != "manifest"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
