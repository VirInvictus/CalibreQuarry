import sys

from vir_tui import reset_terminal

from cquarry_cli.cli import main

if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        reset_terminal()
