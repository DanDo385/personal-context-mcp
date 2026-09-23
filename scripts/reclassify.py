#!/usr/bin/env python
"""Retry classification for objects a classifier outage left pending.

Equivalent to `personal-context-mcp reclassify`. Safe to run on a schedule.
"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    from personal_context_mcp.cli import main

    sys.argv = ["personal-context-mcp", "reclassify", *sys.argv[1:]]
    main()
