import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from monitor import AlertHandler, Matcher, load_config  # noqa: E402

CFG = load_config(Path(__file__).resolve().parents[1] / "config.toml")
M = Matcher(CFG["keywords"], CFG["exact_words"])


@pytest.mark.parametrize("text,expected", [
    ("Шахеди в напрямку ж/м Перемога", ["Перемог"]),
    ("вибухи на Перемозі", ["Перемоз"]),
    ("Рух на Набережній Перемоги", ["Перемог", "Набережн"]),
    ("Набережна заводська", ["Набережн"]),
    ("Придніпровськ - укриття!", ["Придні"]),
    ("в районі Кодаків", ["Кодак"]),
    ("ракета на Авіку", ["авік"]),
    ("в районі аеропорту", ["аеропорт"]),
    ("удар по ТЕС", ["ТЕС"]),
    ("ТЕС!", ["ТЕС"]),
])
def test_hits(text, expected):
    assert M.match(text) == expected


@pytest.mark.parametrize("text", [
    "тест системи оповіщення",
    "інтерес",
    "ТЕСТ",
    "Самарський район, Лівий берег",
    "",
])
def test_misses(text):
    assert M.match(text) == []


def test_mid_word_does_not_match():
    # "авік" must start a word, not appear inside one
    assert M.match("кравікс") == []


def test_latin_lookalikes():
    # "Придні" typed with Latin "i" and "p"; "TEC" fully Latin
    assert M.match("Пpидні") == ["Придні"]
    assert M.match("удар по TEC") == ["ТЕС"]


class FakeNotifier:
    def __init__(self):
        self.sent = []

    async def send(self, title, body, level="critical", url=None):
        self.sent.append((level, title, url))


def test_cooldown_downgrades_repeats():
    now = [0.0]
    notifier = FakeNotifier()
    h = AlertHandler(M, notifier, cooldown=180, clock=lambda: now[0])

    async def scenario():
        await h.handle("dnipromonitor", 1, "Шахед на Перемогу")
        now[0] = 30
        await h.handle("dnipro_alerts", 7, "шахед ж/м Перемога")
        await h.handle("ny_i_dnipro", 9, "Аеропорт - вибух")  # new keyword -> critical
        await h.handle("ny_i_dnipro", 10, "погода")  # no match -> nothing
        now[0] = 500
        await h.handle("dnipromonitor", 2, "знову Перемога")  # cooldown over

    asyncio.run(scenario())
    assert [s[0] for s in notifier.sent] == ["critical", "active", "critical", "critical"]
    assert notifier.sent[0][2] == "https://t.me/dnipromonitor/1"
