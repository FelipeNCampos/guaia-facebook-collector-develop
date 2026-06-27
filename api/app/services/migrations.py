from pathlib import Path

from psycopg import connect


def apply_sql_migrations(db_dsn: str, migrations_path: Path) -> None:
    migrations = sorted(migrations_path.glob("*.sql"))
    if not migrations:
        return

    with connect(db_dsn) as connection:
        connection.autocommit = False
        _ensure_migration_table(connection)

        with connection.cursor() as cursor:
            cursor.execute("SELECT filename FROM schema_migrations;")
            applied = {row[0] for row in cursor.fetchall()}

        for migration_file in migrations:
            if migration_file.name in applied:
                continue

            sql = migration_file.read_text(encoding="utf-8")
            with connection.cursor() as cursor:
                cursor.execute(sql)
                cursor.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s);",
                    (migration_file.name,),
                )
            connection.commit()


def _ensure_migration_table(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              filename TEXT PRIMARY KEY,
              applied_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
            """
        )
    connection.commit()

