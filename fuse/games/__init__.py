"""Registre des jeux supportés."""

from .base import FileMapping, Framework, Game, LayoutError, MappingResult
from .cyberpunk2077 import Cyberpunk2077

GAMES: dict[str, Game] = {g.id: g for g in (Cyberpunk2077(),)}


def get_game(game_id: str) -> Game:
    try:
        return GAMES[game_id]
    except KeyError:
        raise KeyError(f"Jeu inconnu : {game_id!r} (connus : {', '.join(GAMES)})") from None


__all__ = ["GAMES", "get_game", "Game", "FileMapping", "Framework", "LayoutError", "MappingResult"]
