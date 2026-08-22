import ast

filepath = '/home/bdkl/.gitrepos/CalibreQuarry/tests/test_scripts.py'
with open(filepath, 'r') as f:
    source = f.read()

tree = ast.parse(source)

remove = {
    'TestScriptOf', 'TestFindings', 'TestResolveLibraryRoot', 
    'TestPageNumberValue', 'TestIsDefective', 'TestPageNumberScan',
    'TestVisibleChars', 'TestEmptyTextScan', 'TestPctDecode',
    'TestPercentEncodedSpine', 'TestPlaceholderExport',
    'TestOcrSplitDetection', 'TestIsOcrDamaged', 'TestAllIncludesOcr',
    'TestVisibleTextCache', 'TestAuditEpubConnectRo', 'TestContentSections',
    'TestLoadBookCorruptEntry'
}

with open(filepath, 'w') as out:
    # Just write nodes we don't want to remove
    for node in tree.body:
        # Check if it's a class to remove
        if isinstance(node, ast.ClassDef) and node.name in remove:
            continue
            
        # Check if it's assigning audit_epub, pagenum, emptytext, ocr
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any(t in ('audit_epub', 'pagenum', 'emptytext', 'ocr') for t in targets):
                continue

        segment = ast.get_source_segment(source, node)
        out.write(segment + "\n\n")

