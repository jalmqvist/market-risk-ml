#!/usr/bin/env python3
"""Manage the MRML Docker Compose PostgreSQL service using the repo .env.

Usage:
    python scripts/mrml_postgres.py start
    python scripts/mrml_postgres.py status
    python scripts/mrml_postgres.py test
    python scripts/mrml_postgres.py stop

The 'test' command starts PostgreSQL when needed and stops it afterward only
if this invocation started it. It never removes the database volume.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

try:
    from dotenv import dotenv_values
except ImportError:
    raise SystemExit("Install project dependencies first: pip install -e .")

SERVICE = "db"
DEFAULT_TIMEOUT = 90
TEST_FILES = (
    "tests/test_execution_journal.py",
    "tests/test_broker_observations.py",
    "tests/test_account_gate.py",
)


def locate_repo() -> Path:
    # Works both from the repository root and when installed in repo/scripts/.
    candidates = (Path(__file__).resolve().parent.parent, Path.cwd().resolve())
    for root in candidates:
        if (root / "docker-compose.yml").is_file() and (root / ".env").is_file():
            return root
    raise SystemExit("Run from the MRML repo root, with docker-compose.yml and .env present.")


def configuration(root: Path) -> tuple[dict[str, str], str, str, str]:
    values = dotenv_values(root / ".env", interpolate=False)
    required = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise SystemExit("Missing required .env value(s): " + ", ".join(missing))
    db, user, password = (str(values[key]) for key in required)
    # Override inherited shell variables deliberately: .env is authoritative.
    env = dict(os.environ)
    env.update({key: str(values[key]) for key in required})
    # Disable Docker Compose's interactive prompts.
    env["COMPOSE_INTERACTIVE_NO_CLI"] = "1"
    return env, db, user, password


def run(cmd: list[str], *, root: Path, env: dict[str, str],
        check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=root, env=env, text=True, check=check,
        capture_output=capture,
    )


def compose(root: Path, env: dict[str, str], *args: str,
            capture: bool = False) -> subprocess.CompletedProcess[str]:
    return run(
        ["docker", "compose", "--env-file", ".env", *args],
        root=root, env=env, capture=capture,
    )


def container_id(root: Path, env: dict[str, str]) -> str:
    result = compose(root, env, "ps", "-q", SERVICE, capture=True)
    return result.stdout.strip()


def health(root: Path, env: dict[str, str]) -> str:
    cid = container_id(root, env)
    if not cid:
        return "stopped"
    result = run(
        ["docker", "inspect", "--format", "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}", cid],
        root=root, env=env, check=False, capture=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def start(root: Path, env: dict[str, str], timeout: int) -> None:
    print("Starting MRML PostgreSQL (Docker Compose db)...", flush=True)
    compose(root, env, "up", "-d", "--no-recreate", SERVICE)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = health(root, env)
        if status == "healthy":
            print("PostgreSQL is healthy.", flush=True)
            return
        if status in ("exited", "dead"):
            raise RuntimeError(f"PostgreSQL container stopped: {status}")
        time.sleep(2)
    raise RuntimeError(f"PostgreSQL did not become healthy within {timeout}s (state: {health(root, env)}).")


def stop(root: Path, env: dict[str, str]) -> None:
    # Stop only the db service, not unrelated Compose services; no volume deletion.
    print("Stopping MRML PostgreSQL (data volume retained)...", flush=True)
    compose(root, env, "stop", SERVICE)


def test(root: Path, env: dict[str, str], db: str, user: str,
         password: str, timeout: int) -> int:
    was_healthy = health(root, env) == "healthy"
    if not was_healthy:
        start(root, env, timeout)
    try:
        # Encode special characters; do not print the URL (it contains a password).
        userinfo = f"{quote(user, safe='')}:{quote(password, safe='')}"
        dbname = quote(db, safe="")
        test_env = dict(env)
        test_env["MRML_TEST_POSTGRES_URL"] = (
            f"postgresql+psycopg://{userinfo}@127.0.0.1:5432/{dbname}"
        )
        print("Running isolated MRML PostgreSQL tests...", flush=True)
        result = run(
            [sys.executable, "-m", "pytest", "-q", "-rs", *TEST_FILES],
            root=root, env=test_env, check=False,
        )
        return result.returncode
    finally:
        if not was_healthy:
            stop(root, env)
        else:
            print("PostgreSQL was already running; leaving it running.", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "stop", "status", "test"))
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help="Health-check wait in seconds (default: 90)")
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    root = locate_repo()
    env, db, user, password = configuration(root)
    try:
        if args.command == "status":
            print("MRML PostgreSQL:", health(root, env))
        elif args.command == "start":
            start(root, env, args.timeout)
        elif args.command == "stop":
            stop(root, env)
        else:
            return test(root, env, db, user, password, args.timeout)
        return 0
    except (subprocess.CalledProcessError, FileNotFoundError, RuntimeError) as error:
        # Never print command environments, credentials, or the database URL.
        if isinstance(error, subprocess.CalledProcessError):
            print(f"Command failed (exit {error.returncode}).", file=sys.stderr)
        elif isinstance(error, FileNotFoundError):
            print("Docker/Compose or Python executable not found.", file=sys.stderr)
        else:
            print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
