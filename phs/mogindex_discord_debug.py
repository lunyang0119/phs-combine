"""
Live Discord debug runner for the Mogtel search/index prototype.

This script connects to Discord, reads real channel/thread history, and writes
only metadata plus derived index terms into SQLite. It does not store raw
message text.

Examples on the server:
    python mogindex_discord_debug.py discover
    python mogindex_discord_debug.py backfill --start-date 2026-06-01 --end-date 2026-06-02
    python mogindex_discord_debug.py topic-sync --topic-thread-id 1234567890123456789
    python mogindex_debug.py --db /home/ubuntu/mogtel/mogindex/search_index_live_phs_debug.sqlite3 search 유죄
"""

from __future__ import annotations

import argparse
import asyncio
import os
import traceback
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

try:
    import discord
    DISCORD_IMPORT_ERROR = None
except ModuleNotFoundError as exc:  # pragma: no cover - useful server error
    discord = None
    DISCORD_IMPORT_ERROR = exc

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover
    load_dotenv = None

from mogindex_debug import (
    CATEGORY_ID,
    connect,
    discord_snowflake_datetime,
    get_recap,
    initialize_schema,
    insert_message_for_index,
    parse_topic_lines,
    print_inspect,
    print_recap,
    print_search_results,
    reset_debug_data,
    search_messages,
    sync_topic_lines,
    upsert_source,
)


DEFAULT_DB_PATH = Path("/home/ubuntu/mogtel/mogindex/search_index_live_phs_debug.sqlite3")
DEFAULT_PARENT_CHANNEL_IDS = (
    "1399959589121953875",
    "1422784351627644968",
    "1422784292680896534",
    "1431917788557213726",
    "1399959232610177047",
    "1399957143091806239",
)


@dataclass(frozen=True)
class SourceTarget:
    source_id: str
    source_kind: str
    name: str
    parent_channel_id: str | None
    channel: discord.abc.Messageable


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def date_bounds(start_date: date, end_date: date, tz_name: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    start_local = datetime.combine(start_date, time.min, tzinfo=tz)
    end_local = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def display_name(channel: object) -> str:
    parent = getattr(channel, "parent", None)
    if parent and getattr(parent, "name", None):
        return f"{parent.name} / {getattr(channel, 'name', channel.id)}"
    return str(getattr(channel, "name", getattr(channel, "id", "unknown")))


def selected_parent_channel_ids(args: argparse.Namespace) -> list[str]:
    return args.parent_channel_id or list(DEFAULT_PARENT_CHANNEL_IDS)


async def fetch_channel_or_thread(client: discord.Client, channel_id: str):
    cached = client.get_channel(int(channel_id))
    if cached:
        return cached
    return await client.fetch_channel(int(channel_id))


def is_text_channel(channel: object) -> bool:
    return isinstance(channel, discord.TextChannel)


def is_forum_channel(channel: object) -> bool:
    forum_channel_type = getattr(discord, "ForumChannel", None)
    return forum_channel_type is not None and isinstance(channel, forum_channel_type)


def thread_might_have_messages(
    thread: discord.Thread,
    after: datetime | None,
    before: datetime | None,
) -> bool:
    if after is None and before is None:
        return True

    created_at = getattr(thread, "created_at", None)
    if before and created_at and created_at >= before:
        return False

    last_message_id = getattr(thread, "last_message_id", None)
    if after and last_message_id:
        try:
            if discord_snowflake_datetime(str(last_message_id)) <= after:
                return False
        except (TypeError, ValueError):
            return True

    return True


async def iter_archived_threads(channel: object, include_private: bool) -> Iterable[discord.Thread]:
    archived_threads = getattr(channel, "archived_threads", None)
    if archived_threads is None:
        return

    public_attempts = (
        {"limit": None, "private": False},
        {"limit": 100, "private": False},
        {"limit": None},
        {"limit": 100},
    )
    private_attempts = (
        {"limit": None, "private": True},
        {"limit": 100, "private": True},
    )

    seen: set[str] = set()
    for kwargs in public_attempts:
        try:
            async for thread in archived_threads(**kwargs):
                thread_id = str(thread.id)
                if thread_id not in seen:
                    seen.add(thread_id)
                    yield thread
            break
        except TypeError:
            continue
        except discord.Forbidden:
            return

    if not include_private or is_forum_channel(channel):
        return

    for kwargs in private_attempts:
        try:
            async for thread in archived_threads(**kwargs):
                thread_id = str(thread.id)
                if thread_id not in seen:
                    seen.add(thread_id)
                    yield thread
            break
        except TypeError:
            continue
        except discord.Forbidden:
            return


async def gather_targets(
    client: discord.Client,
    parent_channel_ids: Iterable[str],
    *,
    category_id: str,
    include_threads: bool,
    include_private_archived: bool,
    thread_after: datetime | None = None,
    thread_before: datetime | None = None,
) -> list[SourceTarget]:
    targets: list[SourceTarget] = []
    seen: set[str] = set()

    for channel_id in parent_channel_ids:
        channel = await fetch_channel_or_thread(client, channel_id)
        if not (is_text_channel(channel) or is_forum_channel(channel)):
            print(f"skip unsupported channel: {channel_id} ({type(channel).__name__})")
            continue
        if str(getattr(channel, "category_id", None)) != str(category_id):
            print(
                f"warn category mismatch: #{getattr(channel, 'name', channel_id)} "
                f"category={getattr(channel, 'category_id', None)}, expected={category_id}"
            )

        if is_text_channel(channel):
            targets.append(
                SourceTarget(
                    source_id=str(channel.id),
                    source_kind="channel",
                    name=channel.name,
                    parent_channel_id=None,
                    channel=channel,
                )
            )
            seen.add(str(channel.id))
        else:
            print(f"forum parent: {channel.name} ({channel.id}); indexing posts/threads only")

        if not include_threads:
            continue

        for thread in list(channel.threads):
            if str(thread.id) not in seen and thread_might_have_messages(thread, thread_after, thread_before):
                targets.append(
                    SourceTarget(
                        source_id=str(thread.id),
                        source_kind="thread",
                        name=display_name(thread),
                        parent_channel_id=str(channel.id),
                        channel=thread,
                    )
                )
                seen.add(str(thread.id))

        async for thread in iter_archived_threads(channel, include_private_archived):
            if str(thread.id) not in seen and thread_might_have_messages(thread, thread_after, thread_before):
                targets.append(
                    SourceTarget(
                        source_id=str(thread.id),
                        source_kind="thread",
                        name=display_name(thread),
                        parent_channel_id=str(channel.id),
                        channel=thread,
                    )
                )
                seen.add(str(thread.id))

    return targets

async def collect_source(
    conn,
    target: SourceTarget,
    *,
    guild_id: str,
    category_id: str,
    after: datetime,
    before: datetime,
    limit: int | None,
    dry_run: bool,
) -> tuple[int, int]:
    scanned = 0
    indexed = 0
    if not dry_run:
        upsert_source(
            conn,
            target.source_id,
            target.source_kind,
            target.name,
            target.parent_channel_id,
            guild_id=guild_id,
            category_id=category_id,
        )

    async for message in target.channel.history(
        after=after,
        before=before,
        oldest_first=True,
        limit=limit,
    ):
        scanned += 1
        if message.author.bot or message.type != discord.MessageType.default:
            continue
        if dry_run:
            indexed += 1
            continue

        inserted = insert_message_for_index(
            conn,
            message_id=str(message.id),
            source_id=target.source_id,
            channel_id=str(message.channel.id),
            thread_id=target.source_id if target.source_kind == "thread" else None,
            author_id=str(message.author.id),
            author_name=message.author.display_name,
            created_at=message.created_at,
            content=message.content or "",
            search_context=target.name,
            guild_id=guild_id,
        )
        indexed += int(inserted)
    if not dry_run:
        conn.commit()
    return scanned, indexed


async def run_discover(args: argparse.Namespace) -> None:
    client = make_client()

    @client.event
    async def on_ready():
        try:
            targets = await gather_targets(
                client,
                selected_parent_channel_ids(args),
                category_id=args.category_id,
                include_threads=args.include_threads,
                include_private_archived=args.include_private_archived,
            )
            print(f"connected as {client.user}")
            print(f"targets: {len(targets)}")
            for target in targets:
                parent = f", parent={target.parent_channel_id}" if target.parent_channel_id else ""
                print(f"- {target.source_kind}: {target.name} ({target.source_id}{parent})")
        except Exception:
            traceback.print_exc()
        finally:
            await client.close()
            await asyncio.sleep(1.0)

    await client.start(get_token(args.token_env))

async def run_probe(args: argparse.Namespace) -> None:
    after = None
    before = None
    if args.start_date or args.end_date:
        start = args.start_date or args.end_date
        end = args.end_date or args.start_date
        after, before = date_bounds(start, end, args.tz)

    client = make_client()

    @client.event
    async def on_ready():
        try:
            targets = await gather_targets(
                client,
                selected_parent_channel_ids(args),
                category_id=args.category_id,
                include_threads=args.include_threads,
                include_private_archived=args.include_private_archived,
                thread_after=after if args.filter_threads_by_range else None,
                thread_before=before if args.filter_threads_by_range else None,
            )
            print(f"connected as {client.user}")
            print(f"targets: {len(targets)}")
            if after and before:
                print(f"utc bounds: {after.isoformat()} .. {before.isoformat()}")

            checked = 0
            found = 0
            for target in targets:
                if args.max_sources is not None and checked >= args.max_sources:
                    break
                checked += 1
                source_found = 0
                print(f"- {target.name} ({target.source_id})")
                kwargs = {"limit": args.limit_per_source, "oldest_first": False}
                if after:
                    kwargs["after"] = after
                if before:
                    kwargs["before"] = before
                async for message in target.channel.history(**kwargs):
                    if message.author.bot or message.type != discord.MessageType.default:
                        continue
                    source_found += 1
                    found += 1
                    print(
                        f"  {message.created_at.isoformat()} "
                        f"{message.author.display_name} len={len(message.content or '')} "
                        f"{message.jump_url}"
                    )
                if source_found == 0:
                    print("  no visible default user messages")
            print(f"probe done: checked_sources={checked}, found_messages={found}")
        except Exception:
            traceback.print_exc()
        finally:
            await client.close()
            await asyncio.sleep(1.0)

    await client.start(get_token(args.token_env))

async def run_backfill(args: argparse.Namespace) -> None:
    after, before = date_bounds(args.start_date, args.end_date, args.tz)
    client = make_client()

    @client.event
    async def on_ready():
        try:
            targets = await gather_targets(
                client,
                selected_parent_channel_ids(args),
                category_id=args.category_id,
                include_threads=args.include_threads,
                include_private_archived=args.include_private_archived,
                thread_after=after if args.filter_threads_by_range else None,
                thread_before=before if args.filter_threads_by_range else None,
            )
            guild_id = str(targets[0].channel.guild.id) if targets else ""
            with connect(args.db) as conn:
                initialize_schema(conn)
                total_scanned = 0
                total_indexed = 0
                print(f"date range ({args.tz}): {args.start_date} .. {args.end_date}")
                print(f"utc bounds: {after.isoformat()} .. {before.isoformat()}")
                for target in targets:
                    scanned, indexed = await collect_source(
                        conn,
                        target,
                        guild_id=guild_id,
                        category_id=args.category_id,
                        after=after,
                        before=before,
                        limit=args.limit_per_source,
                        dry_run=args.dry_run,
                    )
                    total_scanned += scanned
                    total_indexed += indexed
                    action = "would index" if args.dry_run else "indexed"
                    print(f"- {target.name}: scanned={scanned}, {action}={indexed}")
                print(f"done: scanned={total_scanned}, indexed={total_indexed}")
                if not args.dry_run:
                    print_inspect(conn)
        except Exception:
            traceback.print_exc()
        finally:
            await client.close()
            await asyncio.sleep(1.0)

    await client.start(get_token(args.token_env))


async def run_topic_sync(args: argparse.Namespace) -> None:
    client = make_client()

    @client.event
    async def on_ready():
        try:
            thread = await fetch_channel_or_thread(client, args.topic_thread_id)
            if not isinstance(thread, discord.Thread):
                raise TypeError(f"{args.topic_thread_id} is not a Discord thread")
            parts: list[str] = []
            async for message in thread.history(
                oldest_first=True,
                limit=args.limit,
            ):
                if message.author.bot or message.type != discord.MessageType.default:
                    continue
                if message.content:
                    parts.append(message.content)
            parsed_count = len(parse_topic_lines("\n".join(parts)))
            with connect(args.db) as conn:
                initialize_schema(conn)
                inserted = sync_topic_lines(
                    conn,
                    "\n".join(parts),
                    topic_thread_id=str(thread.id),
                )
                print(f"parsed topic lines={parsed_count}, inserted={inserted}")
                print_inspect(conn)
        except Exception:
            traceback.print_exc()
        finally:
            await client.close()
            await asyncio.sleep(1.0)

    await client.start(get_token(args.token_env))


def make_client() -> discord.Client:
    if discord is None:
        raise SystemExit(
            "discord.py is not installed in this Python environment. "
            "Run live Discord commands on the bot server/venv where main.py works."
        ) from DISCORD_IMPORT_ERROR
    intents = discord.Intents.default()
    intents.guilds = True
    intents.message_content = True
    return discord.Client(intents=intents)


def get_token(env_name: str) -> str:
    if load_dotenv:
        load_dotenv()
    token = os.getenv(env_name)
    if not token:
        raise SystemExit(f"{env_name} is not set")
    return token


def add_common_discord_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--token-env", default="MOG_TOKEN")
    parser.add_argument("--category-id", default=CATEGORY_ID)
    parser.add_argument(
        "--parent-channel-id",
        action="append",
        default=None,
        help="Parent channel ID to scan. Can be repeated. Defaults to the configured allowlist.",
    )
    parser.add_argument("--include-threads", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-private-archived", action="store_true")

def print_term_debug(conn, term: str, limit: int) -> None:
    rows = conn.execute(
        """
        SELECT
            mt.term, mt.count, m.message_date, s.name AS source_name,
            m.author_name, m.jump_url
        FROM message_terms mt
        JOIN messages m ON m.message_pk = mt.message_pk
        JOIN sources s ON s.source_id = mt.source_id
        WHERE mt.term LIKE ?
        ORDER BY m.message_date DESC, mt.count DESC
        LIMIT ?
        """,
        (f"%{term}%", limit),
    ).fetchall()
    if not rows:
        print(f"No indexed terms matching {term!r}.")
        return
    for row in rows:
        print(
            f"[{row['message_date']}] {row['source_name']} / {row['author_name']} "
            f"{row['term']}({row['count']}) -> {row['jump_url']}"
        )

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run live Discord search-index debug tasks.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover_parser = subparsers.add_parser("discover")
    add_common_discord_args(discover_parser)

    probe_parser = subparsers.add_parser("probe")
    add_common_discord_args(probe_parser)
    probe_parser.add_argument("--start-date", type=parse_date)
    probe_parser.add_argument("--end-date", type=parse_date)
    probe_parser.add_argument("--tz", default="Asia/Seoul")
    probe_parser.add_argument("--limit-per-source", type=int, default=1)
    probe_parser.add_argument("--max-sources", type=int)
    probe_parser.add_argument("--filter-threads-by-range", action=argparse.BooleanOptionalAction, default=True)

    backfill_parser = subparsers.add_parser("backfill")
    add_common_discord_args(backfill_parser)
    backfill_parser.add_argument("--start-date", type=parse_date, required=True)
    backfill_parser.add_argument("--end-date", type=parse_date, required=True)
    backfill_parser.add_argument("--tz", default="Asia/Seoul")
    backfill_parser.add_argument("--limit-per-source", type=int)
    backfill_parser.add_argument("--dry-run", action="store_true")
    backfill_parser.add_argument("--filter-threads-by-range", action=argparse.BooleanOptionalAction, default=True)

    topic_parser = subparsers.add_parser("topic-sync")
    topic_parser.add_argument("--token-env", default="MOG_TOKEN")
    topic_parser.add_argument("--topic-thread-id", required=True)
    topic_parser.add_argument("--limit", type=int)

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("query")
    search_parser.add_argument("--start-date")
    search_parser.add_argument("--end-date")
    search_parser.add_argument("--source-id")
    search_parser.add_argument("--limit", type=int, default=10)

    terms_parser = subparsers.add_parser("terms", help="Debug indexed terms matching a string")
    terms_parser.add_argument("term")
    terms_parser.add_argument("--limit", type=int, default=20)

    recap_parser = subparsers.add_parser("recap")
    recap_parser.add_argument("--user-id", required=True)
    recap_parser.add_argument("--start-date")
    recap_parser.add_argument("--end-date")
    recap_parser.add_argument("--source-id")

    subparsers.add_parser("inspect")
    subparsers.add_parser("reset-db", help="Clear all debug index tables in the configured SQLite DB")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "discover":
        asyncio.run(run_discover(args))
    elif args.command == "probe":
        asyncio.run(run_probe(args))
    elif args.command == "backfill":
        asyncio.run(run_backfill(args))
    elif args.command == "topic-sync":
        asyncio.run(run_topic_sync(args))
    elif args.command == "search":
        with connect(args.db) as conn:
            initialize_schema(conn)
            rows = search_messages(
                conn,
                args.query,
                start_date=args.start_date,
                end_date=args.end_date,
                source_id=args.source_id,
                limit=args.limit,
            )
            print_search_results(rows)
    elif args.command == "terms":
        with connect(args.db) as conn:
            initialize_schema(conn)
            print_term_debug(conn, args.term, args.limit)
    elif args.command == "recap":
        with connect(args.db) as conn:
            initialize_schema(conn)
            start, end, topics, keywords = get_recap(
                conn,
                user_id=args.user_id,
                start_date=args.start_date,
                end_date=args.end_date,
                source_id=args.source_id,
            )
            print_recap(start, end, topics, keywords)
    elif args.command == "inspect":
        with connect(args.db) as conn:
            initialize_schema(conn)
            print_inspect(conn)
    elif args.command == "reset-db":
        with connect(args.db) as conn:
            initialize_schema(conn)
            reset_debug_data(conn)
            print(f"Reset {args.db}")


if __name__ == "__main__":
    main()
