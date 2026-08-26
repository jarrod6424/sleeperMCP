"""Yahoo scoring helpers (PPR detection from settings)."""

from __future__ import annotations

from typing import Any

from .parse import indexed_items, to_float


def reception_points(settings: dict[str, Any]) -> float | None:
    """Extract reception scoring from Yahoo settings.stat_modifiers if present."""
    modifiers = settings.get("stat_modifiers") or {}
    stats_node = modifiers.get("stats") if isinstance(modifiers, dict) else None
    for item in indexed_items(stats_node):
        stat = item.get("stat") if isinstance(item, dict) else item
        if not isinstance(stat, dict):
            continue
        # Receptions are Yahoo football stat_id 11.
        if str(stat.get("stat_id")) == "11":
            return to_float(stat.get("value"), 0.0)

    categories = settings.get("stat_categories") or {}
    for item in indexed_items(categories.get("stats") if isinstance(categories, dict) else categories):
        stat = item.get("stat") if isinstance(item, dict) else item
        if not isinstance(stat, dict):
            continue
        display = str(stat.get("display_name") or stat.get("name") or "").lower()
        if display in {"rec", "reception", "receptions"} and "value" in stat:
            return to_float(stat.get("value"), 0.0)
    return None


def ppr_from_reception_points(rec: float | None) -> tuple[float | None, str | None]:
    if rec is None:
        return None, None
    ppr_value = 1.0 if rec >= 1 else (0.5 if rec >= 0.5 else 0.0)
    label = "PPR" if ppr_value == 1 else ("Half-PPR" if ppr_value == 0.5 else "Standard")
    return ppr_value, label
