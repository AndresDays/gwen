import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.engine import make_url

from gwen.config import get_settings


def backup_sqlite(
    source: Path,
    destination_dir: Path,
    retention_days: int,
    clock: Callable[[], datetime] | None = None,
) -> Path:
    now = (clock or (lambda: datetime.now(UTC)))()
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"gwen-{now:%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as backup_db:
        source_db.backup(backup_db)

    cutoff = now - timedelta(days=retention_days)
    for candidate in destination_dir.glob("gwen-*.db"):
        modified = datetime.fromtimestamp(candidate.stat().st_mtime, UTC)
        if candidate != destination and modified < cutoff:
            candidate.unlink()
    return destination


def run() -> None:
    settings = get_settings()
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite":
        raise RuntimeError("El backup local solo admite SQLite.")
    database = url.database
    if not database or database == ":memory:":
        raise RuntimeError("No hay una base SQLite persistente para respaldar.")
    source = Path(database).resolve()
    if not source.is_file():
        raise RuntimeError("No se encontró la base SQLite configurada.")
    destination = backup_sqlite(source, Path("backups"), settings.backup_retention_days)
    print(f"Backup creado: {destination.name}")


if __name__ == "__main__":
    run()
