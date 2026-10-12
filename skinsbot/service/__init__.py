"""Игровой сервис: все операции игрока, админки и фоновых задач."""

from .admin import Admin
from .core import Core, GameError
from .crafting import Crafting
from .inventory import Inventory
from .jobs import Jobs
from .market import Market
from .players import Players
from .shop import Shop
from .social import Social
from .spins import Spins
from ..catalog import Catalog


class Game(Players, Spins, Inventory, Crafting, Market, Social, Shop, Admin, Jobs, Core):
    pass


__all__ = ["Game", "GameError", "Catalog"]
