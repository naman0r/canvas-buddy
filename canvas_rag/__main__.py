import argparse
import asyncio
import json
import os
import shutil

from .answer import answer
from .canvas import Canvas, sync
from .config import Config
from .store import Store


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Local Canvas study companion")
    parser.add_argument("command", nargs="?", default="tui", choices=[
        "tui", "courses", "setup", "sync", "ask", "search", "status", "upcoming", "grades", "doctor"])
    parser.add_argument("question", nargs="*")
    parser.add_argument("--url", help="Canvas HTTPS site URL")
    parser.add_argument("--courses", help="Comma-separated course IDs to cache")
    parser.add_argument("--course", type=int, help="Filter to one course")
    parser.add_argument("--provider", choices=["codex", "opencode", "ollama"])
    parser.add_argument("--model")
    args = parser.parse_intermixed_args()
    config = Config.load()
    if args.url:
        config.url = args.url.rstrip("/")
    if args.provider:
        config.provider = args.provider
    if args.model:
        config.model = args.model
    db = Store(config.home)

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
            config.save()
            await sync(config, db, print)
        elif args.command == "sync":
            if not config.courses:
                raise ValueError("Choose classes with setup first")
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
        elif args.command == "doctor":
            print(json.dumps({"canvas_url": config.url, "canvas_pat_set": bool(config.token),
                "courses": config.courses, "provider": config.provider, "model": config.model or "default",
                "executables": {x: shutil.which(x) for x in ("codex", "opencode", "ollama")},
                "data": str(config.home)}, indent=2))
            print(db.status())

    try:
        if args.command == "tui":
            from .ui import CanvasApp
            CanvasApp(config, db).run()
        else:
            asyncio.run(run())
    except (ValueError, RuntimeError) as e:
        parser.exit(1, f"{e}\n")
    except KeyboardInterrupt:
        pass
    finally:
        db.close()


if __name__ == "__main__":
    main()
