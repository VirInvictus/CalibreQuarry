import sys
from cquarry.cli import main
from cquarry.tui import _reset_terminal

if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        _reset_terminal()
