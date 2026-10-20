"""Watch Dnipro alert channels on Telegram and push critical iPhone alerts via Bark."""

import argparse
import asyncio
import logging
import os
import re
import time
import tomllib
from pathlib import Path

import httpx

log = logging.getLogger("dnipro-alerts")

BASE_DIR = Path(__file__).resolve().parent

# Latin letters that look like Cyrillic ones; channel posts sometimes mix them.
_LOOKALIKES = str.maketrans(
    "aceiopxyAEICOPXTHKMB",
    "асеіорхуАЕІСОРХТНКМВ",
)
_APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'", "`": "'", "‘": "'"})


def normalize(text: str) -> str:
    return text.translate(_APOSTROPHES).translate(_LOOKALIKES)


class Matcher:
    """Keyword matcher.

    keywords:    case-insensitive, matched at the start of a word ("Придні" -> "Придніпровськ").
    exact_words: case-sensitive, whole word only ("ТЕС" must not hit "тест").
    """

    def __init__(self, keywords: list[str], exact_words: list[str] = ()):
        self.patterns = [
            (kw, re.compile(r"(?<!\w)" + re.escape(normalize(kw).lower())))
            for kw in keywords
        ] + [
            (w, re.compile(r"(?<!\w)" + re.escape(normalize(w)) + r"(?!\w)"))
            for w in exact_words
        ]
        self._case_sensitive = set(exact_words)

    def match(self, text: str) -> list[str]:
        if not text:
            return []
        norm = normalize(text)
        lower = norm.lower()
        return [
            kw
            for kw, pattern in self.patterns
            if pattern.search(norm if kw in self._case_sensitive else lower)
        ]


class Notifier:
    def __init__(self, bark_key: str, server: str = "https://api.day.app", dry_run: bool = False):
        self.url = f"{server.rstrip('/')}/{bark_key}"
        self.dry_run = dry_run
        self.http = httpx.AsyncClient(timeout=10)

    async def send(self, title: str, body: str, level: str = "critical", url: str | None = None):
        payload = {"title": title, "body": body, "level": level, "group": "dnipro"}
        if level == "critical":
            payload.update(volume=10, sound="alarm")
        if url:
            payload["url"] = url
        if self.dry_run:
            log.info("[dry-run] would notify: %s", payload)
            return
        for attempt in range(3):
            try:
                resp = await self.http.post(self.url, json=payload)
                resp.raise_for_status()
                log.info("notified (%s): %s", level, title)
                return
            except httpx.HTTPError as e:
                log.warning("notify attempt %d failed: %s", attempt + 1, e)
                await asyncio.sleep(2 ** attempt)
        log.error("giving up on notification: %s", title)


class AlertHandler:
    """Turns matching messages into notifications.

    The channels often repeat the same event, so after a critical alert for a keyword,
    further mentions of it within `cooldown` seconds go out as normal notifications.
    """

    def __init__(self, matcher: Matcher, notifier: Notifier, cooldown: float, clock=time.monotonic):
        self.matcher = matcher
        self.notifier = notifier
        self.cooldown = cooldown
        self.clock = clock
        self.last_critical: dict[str, float] = {}

    async def handle(self, channel: str, msg_id: int, text: str) -> list[str]:
        hits = self.matcher.match(text)
        if not hits:
            return hits
        now = self.clock()
        fresh = [kw for kw in hits if now - self.last_critical.get(kw, -1e9) > self.cooldown]
        body = text.strip()
        if len(body) > 200:
            body = body[:200] + "…"
        url = f"https://t.me/{channel}/{msg_id}"
        if fresh:
            for kw in hits:
                self.last_critical[kw] = now
            await self.notifier.send(f"⚠️ Дніпро: {', '.join(hits)}", body, "critical", url)
        else:
            await self.notifier.send(f"Дніпро (also @{channel}): {', '.join(hits)}", body, "active", url)
        return hits


def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_dotenv(path: Path):
    """Minimal .env loader so the script also works outside systemd."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def make_client():
    from telethon import TelegramClient

    return TelegramClient(
        str(BASE_DIR / os.environ.get("TG_SESSION", "dnipro")),
        int(os.environ["TG_API_ID"]),
        os.environ["TG_API_HASH"],
    )


async def backtest(client, channels: list[str], matcher: Matcher, limit: int):
    for channel in channels:
        total = hits = 0
        print(f"\n=== @{channel} (last {limit}) ===")
        async for msg in client.iter_messages(channel, limit=limit):
            total += 1
            found = matcher.match(msg.raw_text or "")
            if found:
                hits += 1
                snippet = (msg.raw_text or "").replace("\n", " ")[:120]
                print(f"{msg.date:%m-%d %H:%M} {found} {snippet}")
        print(f"--> {hits}/{total} messages matched")


async def run(client, channels: list[str], handler: AlertHandler, notifier: Notifier):
    from telethon import events
    from telethon.tl.functions.channels import JoinChannelRequest

    # Telegram only pushes updates for channels the account has joined.
    for channel in channels:
        try:
            await client(JoinChannelRequest(channel))
        except Exception as e:
            log.warning("could not join @%s: %s", channel, e)

    @client.on(events.NewMessage(chats=channels))
    async def on_message(event):
        username = getattr(event.chat, "username", None) or str(event.chat_id)
        hits = await handler.handle(username, event.id, event.raw_text or "")
        if hits:
            log.info("match in @%s #%s: %s", username, event.id, hits)

    log.info("listening to %s", ", ".join("@" + c for c in channels))
    await notifier.send("Dnipro monitor started", "Listening: " + ", ".join(channels), "passive")
    await client.run_until_disconnected()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(BASE_DIR / "config.toml"))
    parser.add_argument("--login", action="store_true", help="interactive Telegram login, then exit")
    parser.add_argument("--test-notify", action="store_true", help="send a critical test alert, then exit")
    parser.add_argument("--backtest", type=int, metavar="N", help="match the last N posts per channel, then exit")
    parser.add_argument("--dry-run", action="store_true", help="log matches instead of notifying")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv(BASE_DIR / ".env")
    cfg = load_config(Path(args.config))
    channels = [c.lstrip("@") for c in cfg["channels"]]
    matcher = Matcher(cfg.get("keywords", []), cfg.get("exact_words", []))

    if args.test_notify:
        notifier = Notifier(os.environ["BARK_KEY"], os.environ.get("BARK_SERVER", "https://api.day.app"))
        asyncio.run(notifier.send("⚠️ Test alert", "If you hear this on mute, it works."))
        return

    client = make_client()
    if args.login:
        with client:
            print("Logged in as", client.loop.run_until_complete(client.get_me()).username)
        return
    if args.backtest:
        with client:
            client.loop.run_until_complete(backtest(client, channels, matcher, args.backtest))
        return

    notifier = Notifier(
        os.environ.get("BARK_KEY", ""),
        os.environ.get("BARK_SERVER", "https://api.day.app"),
        dry_run=args.dry_run,
    )
    handler = AlertHandler(matcher, notifier, cfg.get("cooldown_seconds", 180))
    with client:
        client.loop.run_until_complete(run(client, channels, handler, notifier))


if __name__ == "__main__":
    main()
