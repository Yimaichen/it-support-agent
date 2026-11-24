"""Create a protected, redacted COPY of an old database; never edit the source.

Run: uv run python -m src.migrate_privacy --source runtime/support.sqlite3 --output /private/path/redacted.sqlite3
The source/backups may still contain secrets and need separate controlled retention.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from urllib.parse import quote

from src.privacy import sanitize


def migrate_copy(source: Path, output: Path) -> dict:
    source, output = source.resolve(), output.resolve()
    if source == output or not source.is_file():
        raise ValueError("source must exist and output must be a different new file")
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    report = {
        "tables": {},
        "source_modified": False,
        "scope": "best_effort_known_secret_patterns; original remains sensitive",
    }
    with sqlite3.connect(f"file:{quote(str(source))}?mode=ro", uri=True) as old:
        with sqlite3.connect(output) as new:
            old.backup(new)
            new.execute("PRAGMA secure_delete=ON")
            tables = {
                r[0] for r in new.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            for table in sorted(
                tables
                & {
                    "issues",
                    "messages",
                    "resolution_history",
                    "handoffs",
                    "agent_turns",
                    "turn_budgets",
                }
            ):
                columns = [r[1] for r in new.execute(f'PRAGMA table_info("{table}")')]
                fields = [
                    c
                    for c in columns
                    if c.endswith("_json") or (table == "issues" and c == "title")
                ]
                changed = 0
                for row in new.execute(f'SELECT rowid,* FROM "{table}"').fetchall():
                    rowid, values = row[0], dict(zip(columns, row[1:]))
                    for field in fields:
                        value = values[field]
                        if not isinstance(value, str):
                            continue
                        parsed = json.loads(value) if field.endswith("_json") else value
                        cleaned = sanitize(parsed)
                        result = (
                            json.dumps(cleaned, ensure_ascii=False)
                            if field.endswith("_json")
                            else cleaned
                        )
                        if cleaned != parsed:
                            new.execute(
                                f'UPDATE "{table}" SET "{field}"=? WHERE rowid=?', (result, rowid)
                            )
                            changed += 1
                report["tables"][table] = {"changed_fields": changed}
            new.commit()
            new.execute("VACUUM")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(migrate_copy(args.source, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
