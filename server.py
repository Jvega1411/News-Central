#!/usr/bin/env python3
# News Central: local world-news reader. Entry point; the code lives in newscentral/.
# Standard library only, Python 3.8+.
import sys

from newscentral.cli import main

if __name__ == "__main__":
    sys.exit(main())
