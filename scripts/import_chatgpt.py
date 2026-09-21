#!/usr/bin/env python
"""Import a ChatGPT export. Thin wrapper over `personal-context import`."""

from __future__ import annotations

import sys

from scripts._common import run_import

if __name__ == "__main__":
    sys.exit(run_import("chatgpt"))
