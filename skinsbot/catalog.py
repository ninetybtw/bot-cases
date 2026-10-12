"""Каталог в памяти: предметы, кейсы, уровни крутки, цели апгрейдера."""

from __future__ import annotations

import bisect
import json
import statistics
from pathlib import Path

from . import rules


class Catalog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.load()

    def load(self) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.mtime = self.path.stat().st_mtime
        self.generated_at = data["generated_at"]
        self.usd_rub = data.get("usd_rub")
        self.items: dict[str, dict] = data["items"]
        self.case_list: list[dict] = data["cases"]
        self.cases: dict[str, dict] = {c["id"]: c for c in self.case_list}
        self.levels: dict[str, dict] = {lv["id"]: lv for lv in data.get("levels", [])}
        self.history: dict = data.get("price_history", {})
        cheapest = min(self.case_list, key=lambda c: c["price_cents"])
        self.free_case_id = cheapest["id"]
        self.unit_cents = cheapest["price_cents"]  # «цена кейса» для наград колеса и заданий
        self.targets = sorted(
            (p, item_id, key) for item_id, it in self.items.items() for key, p in it["prices"].items()
        )
        self.target_prices = [t[0] for t in self.targets]

    def reload_if_changed(self) -> bool:
        if self.path.stat().st_mtime != self.mtime:
            self.load()
            return True
        return False

    def case_price(self, case_id: str) -> int:
        return self.cases[case_id]["price_cents"]

    def level_price(self, level: str) -> int:
        return self.levels[level]["price_cents"]

    def price(self, catalog_id: str, wear: str | None, stattrak: bool) -> int:
        return int(self.items[catalog_id]["prices"][rules.price_key(wear, stattrak)])

    def tier_values(self) -> dict[str, float]:
        """Средняя опорная цена ступени по активным кейсам (для проверки конфига)."""
        out = {}
        for tier in rules.CASE_TIERS:
            ids = {i for c in self.case_list for i in c["pools"].get(tier, [])}
            out[tier] = statistics.mean(rules.item_expected_cents(self.items[i]) for i in ids)
        return out

    def targets_between(self, lo: float, hi: float) -> list[tuple[int, str, str]]:
        start = bisect.bisect_left(self.target_prices, lo)
        end = bisect.bisect_right(self.target_prices, hi)
        return self.targets[start:end]

    def rtps(self) -> dict[str, float]:
        out = {}
        for c in self.case_list:
            out[c["name"]] = rules.pool_expected_cents(rules.case_odds(), c["pools"], self.items) / c["price_cents"]
        for lv in self.levels.values():
            out[lv["name"]] = rules.pool_expected_cents(rules.level_odds(lv["id"]), lv["pools"], self.items) / lv["price_cents"]
        return out
