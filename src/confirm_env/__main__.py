"""Allow ``python -m confirm_env``."""

from __future__ import annotations

import sys

from confirm_env.cli import main

if __name__ == "__main__":
    sys.exit(main())
