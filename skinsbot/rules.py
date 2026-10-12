"""Числа из ТЗ: ступени, шансы, износ, лимиты. Меняются здесь, а не по коду."""

from __future__ import annotations

# Единая шкала редкостей (ТЗ, «Система редкостей», п. 1). Бот работает только с CS2,
# поэтому из кейсов выпадают ступени от Редкого до Секретного.
TIERS = {
    "common": {"name": "Обычный", "icon": "○", "color": "#9aa1ab"},
    "uncommon": {"name": "Необычный", "icon": "◇", "color": "#5fbf4a"},
    "rare": {"name": "Редкий", "icon": "◆", "color": "#4b7bff"},
    "epic": {"name": "Эпик", "icon": "⬢", "color": "#9a52ff"},
    "legendary": {"name": "Легендарный", "icon": "★", "color": "#e04fd2"},
    "mythic": {"name": "Мифический", "icon": "✹", "color": "#ec3b3b"},
    "secret": {"name": "Секретный", "icon": "♛", "color": "#f2c440"},
    "divine": {"name": "Божественный", "icon": "✺", "color": "#ffffff"},
    "exclusive": {"name": "Эксклюзив", "icon": "◈", "color": "#111111"},
}
TIER_ORDER = list(TIERS)

# Шансы ступеней в кейсе CS2 (раскрыты Valve), сумма 100%.
CASE_TIER_ODDS = {
    "rare": 0.7992,
    "epic": 0.1598,
    "legendary": 0.0320,
    "mythic": 0.0064,
    "secret": 0.0026,
}
# Две самые частые ступени отдают шанс при удаче, остальные умножаются.
LUCK_DONORS = ("rare", "epic")

# Износ: диапазон float, доля выпадения, запасной множитель цены.
WEAR_RANGES = {
    "FN": (0.00, 0.07),
    "MW": (0.07, 0.15),
    "FT": (0.15, 0.38),
    "WW": (0.38, 0.45),
    "BS": (0.45, 1.00),
}
WEAR_WEIGHTS = {"FN": 0.03, "MW": 0.24, "FT": 0.33, "WW": 0.24, "BS": 0.16}
WEAR_FALLBACK_MULT = {"FN": 1.5, "MW": 1.2, "FT": 1.0, "WW": 0.85, "BS": 0.7}
WEAR_NAMES = {
    "FN": "Factory New",
    "MW": "Minimal Wear",
    "FT": "Field-Tested",
    "WW": "Well-Worn",
    "BS": "Battle-Scarred",
}
STATTRAK_CHANCE = 0.10
STATTRAK_FALLBACK_MULT = 1.3

# Лестница опорных цен (запасная, если у лота нет рыночной цены), игровые $.
LADDER = {
    "common": 10,
    "uncommon": 30,
    "rare": 100,
    "epic": 400,
    "legendary": 2000,
    "mythic": 10000,
    "secret": 50000,
}

TARGET_RTP = 0.90  # цена спина = средний дроп / 0,9
QUICK_SELL = 0.60  # быстрая продажа системе

# Гарантии: (ступени, мягкий старт, жёсткий потолок). С мягкого старта шанс растёт на 5 п.п. за спин.
PITY = {
    "epic": (("epic", "legendary", "mythic", "secret"), 51, 60),
    "legendary": (("legendary", "mythic", "secret"), 281, 300),
}
PITY_STEP = 0.05

# Энергия: 1 бесплатный спин раз в 3 часа, копится до 3.
ENERGY_PERIOD = 3 * 3600
ENERGY_MAX = 3

INVENTORY_SLOTS = 200
SPINS_PER_REQUEST = (1, 5, 10)
BREAK_REMINDER_SPINS = 50

# Апгрейдер.
UPGRADE_HOUSE = 0.85
UPGRADE_FAIL_BONUS = 0.03
UPGRADE_SCRAP = 0.10
UPGRADE_MAX_ITEMS = 3
UPGRADE_DAILY_LIMIT = 10
UPGRADE_MAX_CHANCE = 0.75
UPGRADE_MULTIPLIERS = (1.5, 2, 3, 5, 10)

# Контракт «5 в 1».
CONTRACT_SIZE = 5
CONTRACT_NEXT = {"rare": "epic", "epic": "legendary", "legendary": "mythic", "mythic": "secret"}
CONTRACT_SUCCESS = {"rare": 1.0, "epic": 0.7, "legendary": 0.7, "mythic": 0.7}

# Ежедневное колесо: доля цены самого дешёвого кейса или бесплатные спины.
DAILY_WHEEL = [
    {"id": "c05", "label": "0,5 кейса", "cases": 0.5, "p": 0.55},
    {"id": "c1", "label": "1 кейс", "cases": 1.0, "p": 0.25},
    {"id": "c25", "label": "2,5 кейса", "cases": 2.5, "p": 0.12},
    {"id": "spin", "label": "1 спин", "spins": 1, "p": 0.06},
    {"id": "c10", "label": "10 кейсов", "cases": 10.0, "p": 0.02},
]

# Ежедневные задания: цель и награда в долях цены самого дешёвого кейса.
DAILY_TASKS = [
    {"id": "spin3", "title": "Открой 3 кейса", "goal": 3, "reward_cases": 0.4},
    {"id": "sell1", "title": "Продай 1 предмет", "goal": 1, "reward_cases": 0.3},
    {"id": "upgrade1", "title": "Попробуй апгрейд", "goal": 1, "reward_cases": 0.6},
]

START_BALANCE_CASES = 5  # стартовый баланс в ценах самого дешёвого кейса
START_FREE_SPINS = 3


def price_key(wear: str | None, stattrak: bool) -> str:
    return ("ST_" if stattrak else "") + (wear or "-")


def wear_distribution(item: dict) -> dict[str, float]:
    """Доли износа среди возможных для скина, пересчитанные до 100%."""
    wears = item.get("wears") or []
    if not wears:
        return {"-": 1.0}
    total = sum(WEAR_WEIGHTS[w] for w in wears)
    return {w: WEAR_WEIGHTS[w] / total for w in wears}


def item_expected_value(item: dict) -> float:
    st = STATTRAK_CHANCE if item.get("stattrak") else 0.0
    ev = 0.0
    for wear, p in wear_distribution(item).items():
        w = None if wear == "-" else wear
        ev += p * (1 - st) * item["prices"][price_key(w, False)]
        if st:
            ev += p * st * item["prices"][price_key(w, True)]
    return ev


def case_expected_value(case: dict, items: dict[str, dict]) -> float:
    ev = 0.0
    for tier, p in CASE_TIER_ODDS.items():
        pool = case["pools"].get(tier) or []
        if pool:
            ev += p * sum(item_expected_value(items[i]) for i in pool) / len(pool)
    return ev
