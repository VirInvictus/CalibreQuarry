import os
import shutil
import sqlite3
import sys
import tempfile
from urllib.parse import quote

def db_uri_ro(path: str) -> str:
    """Read-only SQLite URI for a path."""
    return f"file:{quote(str(path))}?mode=ro"

def connect_ro(db_path: str) -> tuple[sqlite3.Connection, str | None]:
    """Open the database read-only; fall back to a temp copy if locked."""
    conn = sqlite3.connect(db_uri_ro(db_path), uri=True)
    try:
        conn.execute("SELECT 1 FROM books LIMIT 1")
        return conn, None
    except sqlite3.OperationalError as e:
        conn.close()
        if "locked" not in str(e).lower():
            raise
    print(
        "NOTE: Database is locked (Calibre is running). "
        "Reading from a snapshot copy.",
        file=sys.stderr,
    )
    fd, tmp = tempfile.mkstemp(suffix=".db", prefix="cquarry_")
    os.close(fd)
    shutil.copy2(db_path, tmp)
    for suffix in ("-wal", "-shm"):
        src = str(db_path) + suffix
        if os.path.exists(src):
            shutil.copy2(src, tmp + suffix)
    return sqlite3.connect(db_uri_ro(tmp), uri=True), tmp

def cleanup_tmp(tmp_path: str | None):
    if tmp_path:
        for suffix in ("", "-wal", "-shm"):
            path = tmp_path + suffix
            try:
                os.unlink(path)
            except OSError:
                pass
