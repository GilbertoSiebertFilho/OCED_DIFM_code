# -*- coding: utf-8 -*-
"""python -m oced_clean ... = the oced-clean command (oced_clean/cli.py)."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
