import argparse
import asyncio
import os
import sys
import sqlite3
from importlib.metadata import version

import httpx

from .answer import answer
from .canvas import Canvas, sync
from .config import Config
from .store import STALE_HOURS, Store
from .diagnostics import doctor, self_test


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Local Canvas study companion")
    parser.add_argument("--version", action="version", version=f"Canvas Buddy {version('canvas-buddy')}")
    parser.add_argument("command", nargs="?", default="tui", choices=[
        "tui", "demo", "courses", "setup", "sync", "ask", "search", "status", "upcoming", "grades", "digest",
        "changes", "doctor", "self-test"])
    parser.add_argument("question", nargs="*")
    parser.add_argument("--url", help="Canvas HTTPS site URL")
    parser.add_argument("--courses", help="Comma-separated course IDs to cache")
    parser.add_argument("--course", type=int, help="Filter to one course")
    parser.add_argument("--provider", choices=["codex", "opencode", "ollama"])
    parser.add_argument("--model")
    parser.add_argument("--if-stale", action="store_true",
                        help=f"With sync: skip when the cache is under {STALE_HOURS} hours old")
    parser.add_argument("--days", type=int, default=14, help="With digest: upcoming window")
    args = parser.parse_intermixed_args()
    if args.command == "self-test":
        asyncio.run(self_test())
        return
    if args.command == "demo":
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            parser.exit(1, "The demo needs an interactive terminal. Try canvas-buddy self-test instead.\n")
        from .demo import run as run_demo
        run_demo()
        return
    try:
        config = Config.load()
    except (ValueError, OSError) as e:
        parser.exit(1, f"{e}\n")
    if args.url:
        url = args.url.strip().rstrip("/").removesuffix("/api/v1")
        if config.url and url != config.url:
            parser.exit(1, "Change Canvas site and token together using canvas-buddy setup, or use a new CANVAS_BUDDY_HOME.\n")
        config.url = url
    if args.provider:
        config.provider = args.provider
    if args.model:
        config.model = args.model
    try:
        db = Store(config.home)
    except (OSError, sqlite3.Error) as e:
        parser.exit(1, f"Cannot open local data: {e}\n")

    async def run():
        if args.command == "courses":
            async with Canvas(config) as api:
                for c in await api.courses():
                    print(f"{c['id']}\t{c.get('name', '(unavailable)')}\t{(c.get('term') or {}).get('name', '')}")
        elif args.command == "setup":
            if not args.courses:
                raise ValueError("Use the TUI /setup, or setup --url URL --courses ID,ID")
            config.courses = list(dict.fromkeys(int(x.strip()) for x in args.courses.split(",")))
            async with Canvas(config) as api:
                available = {c["id"] for c in await api.courses() if c.get("name")}
            if not set(config.courses) <= available:
                raise ValueError("One or more course IDs are not available to this account")
            async with Canvas(config) as api:
                profile = await api.one("users/self/profile")
            db.bind(config.url, str(profile["id"]))
            config.save_token()
            config.save()
            await sync(config, db, print)
        elif args.command == "sync":
            if not config.courses:
                raise ValueError("Choose classes with setup first")
            if args.if_stale and db.documents() and not db.stale(STALE_HOURS):
                print(f"Cache is under {STALE_HOURS} hours old; skipping sync.")
                return
            await sync(config, db, print)
        elif args.command in {"ask", "search"}:
            q = " ".join(args.question)
            if not q:
                raise ValueError("Enter a question or search text")
            if args.command == "ask":
                print((await answer(config, db, q, args.course))[0])
            else:
                for r in await db.search(q, config, args.course):
                    print(f"{r['title']}\n{r['url']}\n{r['text']}\n")
        elif args.command == "status":
            print(db.status())
        elif args.command == "upcoming":
            for r in db.upcoming(args.course):
                print(f"{r['due']}\t{r['title']}\t{r['state']}\n{r['url']}")
        elif args.command == "grades":
            print(db.grades(args.course))
        elif args.command == "digest":
            print(db.dashboard(args.course, days=args.days))
        elif args.command == "changes":
            print(db.changes_markdown(args.course))
        elif args.command == "doctor":
            await doctor(config, db)

    try:
        if args.command == "tui" or (args.command == "setup" and not args.courses):
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                raise ValueError("The TUI needs an interactive terminal. Try canvas-buddy --help or canvas-buddy self-test.")
            from .ui import CanvasApp
            CanvasApp(config, db, setup=args.command == "setup").run()
        else:
            asyncio.run(run())
    except (ValueError, RuntimeError, OSError, httpx.HTTPError) as e:
        parser.exit(1, f"{config.error(e)}\n")
    except KeyboardInterrupt:
        pass
    finally:
        db.close()


if __name__ == "__main__":
    main()
