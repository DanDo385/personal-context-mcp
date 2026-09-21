#!/usr/bin/env python
"""Retry classification for objects a classifier outage left pending.

Equivalent to `personal-context reclassify`. Safe to run on a schedule.
"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    from personal_context.cli import main

    sys.argv = ["personal-context", "reclassify", *sys.argv[1:]]
    main()
