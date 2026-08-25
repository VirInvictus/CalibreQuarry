import sys

from cquarry_cli.cli import main
from vir_tui.tui import _reset_terminal

if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        _reset_terminal()
