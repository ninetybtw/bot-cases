"""Каталог в памяти: предметы, свои кейсы, цели апгрейдера."""

from __future__ import annotations

import bisect
import json
from pathlib import Path

from . import odds, rules


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
        self.history: dict = data.get("price_history", {})
        free = [c for c in self.case_list if c.get("free")] or [min(self.case_list, key=lambda c: c["price_cents"])]
        self.free_case_id = free[0]["id"]
        unit = rules.CFG["cases"].get("unit_case")
        # «цена кейса» для наград колеса, заданий и стартового баланса
        self.unit_cents = self.cases[unit]["price_cents"] if unit in self.cases else \
            sorted(c["price_cents"] for c in self.case_list)[len(self.case_list) // 2]
        self.targets = sorted(
            (p, item_id, key) for item_id, it in self.items.items() for key, p in it["prices"].items()
        )
        self.target_prices = [t[0] for t in self.targets]
        self.max_price_cents = self.target_prices[-1]

    def reload_if_changed(self) -> bool:
        if self.path.stat().st_mtime != self.mtime:
            self.load()
            return True
        return False

    def case_price(self, case_id: str) -> int:
        return self.cases[case_id]["price_cents"]

    def price(self, catalog_id: str, wear: str | None, stattrak: bool) -> int:
        return int(self.items[catalog_id]["prices"][rules.price_key(wear, stattrak)])

    def item_chance(self, catalog_id: str, case_id: str | None = None) -> float:
        """Шанс предмета в кейсе (без удачи и гарантий). Без кейса — в первом кейсе, где он есть."""
        if case_id not in self.cases:
            case_id = next(iter(self.items[catalog_id].get("cases") or []), None)
        return self.cases[case_id]["weights"].get(catalog_id, 0.0) if case_id in self.cases else 0.0

    def targets_between(self, lo: float, hi: float) -> list[tuple[int, str, str]]:
        start = bisect.bisect_left(self.target_prices, lo)
        end = bisect.bisect_right(self.target_prices, hi)
        return self.targets[start:end]

    def contract_max_total(self) -> int:
        """Самый дорогой контракт: сумма × наибольший множитель не выше самого дорогого предмета каталога,
        иначе результат упирается в потолок и средний возврат падает ниже 0,8."""
        top = max(m for m, _ in rules.CFG["contracts"]["multipliers"])
        return int(self.max_price_cents / top)

    def rtps(self) -> dict[str, float]:
        """Долгосрочный возврат каждого кейса с мягкими гарантиями."""
        return {c["name"]: rules.case_expected_cents(c, self.items, odds.consolidated(c["tier_odds"])) / c["price_cents"]
                for c in self.case_list}
