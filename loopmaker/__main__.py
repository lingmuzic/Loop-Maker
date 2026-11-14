"""Allows ``python -m loopmaker ...``."""

import sys

from .cli import main

sys.exit(main())
