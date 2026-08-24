import re

path = '/home/bdkl/.gitrepos/CalibreQuarry/src/cquarry_cli/tui.py'
with open(path, 'r') as f:
    content = f.read()

# Fix imports
content = content.replace("from vir_tui import (", "from vir_tui import (")
content = content.replace("_Cancelled,", "CancelledError,")
content = content.replace("_close_screen,", "close_screen,")
content = content.replace("_open_screen,", "open_screen,")
content = content.replace("_reset_terminal,", "reset_terminal,")

content = content.replace("except _Cancelled:", "except CancelledError:")

content = content.replace("stdscr = _open_screen()", "stdscr = open_screen()")
content = content.replace("_close_screen()", "close_screen()")
content = content.replace("_reset_terminal()", "reset_terminal()")

# Fix select_main
tui_select_old = 'return tui_select("CalibreQuarry", sections)'
tui_select_new = '''
    letter_keys = {
        "Change Database": ("s", "self"),
        "Quit": ("q", None)
    }
    aliases = {
        "s": (4, 0),
        "q": None,
        "quit": None
    }
    return tui_select("CalibreQuarry", sections, aliases=aliases, letter_keys=letter_keys)
'''
content = content.replace(tui_select_old, tui_select_new.strip())

with open(path, 'w') as f:
    f.write(content)

