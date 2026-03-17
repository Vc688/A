import os
import traceback

from waitress import serve


def log(message: str) -> None:
    print(f"[serve_waitress] {message}", flush=True)


def main() -> None:
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8010"))
    threads = int(os.getenv("WAITRESS_THREADS", "8"))
    log(f"booting with host={host} port={port} threads={threads}")
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    log(f"OPENAI_API_KEY present={bool(api_key)} length={len(api_key)}")
    try:
        log("importing app module")
        from app import app, ensure_admin, init_db

        log("initializing database")
        init_db()
        log("ensuring admin user")
        ensure_admin()
        log("starting waitress")
        serve(app, host=host, port=port, threads=threads)
    except Exception:
        log("startup failed with exception")
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
