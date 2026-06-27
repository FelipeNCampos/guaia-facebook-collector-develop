from psycopg import connect


def assert_database_connection(db_dsn: str) -> None:
    with connect(db_dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1;")
            cursor.fetchone()

