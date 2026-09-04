import os
import sqlite3
from datetime import UTC, datetime, timedelta

from gwen.backup import backup_sqlite


def test_backup_sqlite_creates_valid_copy_and_removes_expired(tmp_path) -> None:
    source = tmp_path / "gwen.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE memories (content TEXT)")
        connection.execute("INSERT INTO memories VALUES ('café')")

    backups = tmp_path / "backups"
    backups.mkdir()
    expired = backups / "gwen-older.db"
    expired.write_bytes(b"old")
    now = datetime(2026, 9, 4, 18, tzinfo=UTC)
    old_timestamp = (now - timedelta(days=30)).timestamp()
    os.utime(expired, (old_timestamp, old_timestamp))

    result = backup_sqlite(source, backups, 14, lambda: now)

    assert result.is_file()
    assert not expired.exists()
    with sqlite3.connect(result) as connection:
        assert connection.execute("SELECT content FROM memories").fetchone() == ("café",)
