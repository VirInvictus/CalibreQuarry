import re

with open('tests/test_modes.py', 'r') as f:
    text = f.read()

# Just move `_SCHEMA = ... ` below the imports!
match = re.search(r'_SCHEMA = """[\s\S]*?"""\n', text)
if match:
    schema_block = match.group(0)
    text = text.replace(schema_block, '')
    imports_end = text.rfind('import')
    line_end = text.find('\n', imports_end)
    text = text[:line_end+1] + "\n" + schema_block + text[line_end+1:]

with open('tests/test_modes.py', 'w') as f:
    f.write(text)
