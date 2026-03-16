import os

from waitress import serve

from app import app, ensure_admin, init_db


def main() -> None:
    init_db()
    ensure_admin()
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8010"))
    threads = int(os.getenv("WAITRESS_THREADS", "8"))
    serve(app, host=host, port=port, threads=threads)


if __name__ == "__main__":
    main()
