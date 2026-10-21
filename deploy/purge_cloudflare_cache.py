#!/usr/bin/env python3
"""Clear edge HTML after a release changes Next.js asset hashes.

Run only after nginx points at the healthy new colour. Old cached HTML can
refer to JavaScript chunks that disappear when the previous colour stops.
"""

import sys

from cloudflare_cache_rule import call, env


def main() -> int:
    cfg = env()
    token = cfg.get("FICATLAS_CF_API_TOKEN")
    zone = cfg.get("FICATLAS_CF_ZONE_ID")
    if not token or not zone:
        print("Cloudflare cache refresh unavailable: token or zone is missing.")
        return 1
    result = call(token, f"/zones/{zone}/purge_cache", "POST",
                  {"purge_everything": True})
    if not result.get("success"):
        print("Cloudflare cache refresh failed:", result.get("errors"))
        return 1
    print("Cloudflare cache cleared for the new release.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
