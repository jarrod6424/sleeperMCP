"""
Shared advice-tool envelope.

New recommendation tools (waiver_advice, grade_team, pick-aware analyze_trade)
return the same outer fields so an agent always gets a verdict, reasons,
sources, and caveats — even when a nested payload is tool-specific.

`format` for brand-new tools uses the FRD snake_case keys. `analyze_trade`
keeps the existing FantasyCalc camelCase `format` object for backward
compatibility and adds envelope fields alongside it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def format_block(fmt: dict[str, Any] | None) -> dict[str, Any]:
    """FRD envelope format from a FantasyCalc-style league_format dict."""
    fmt = fmt or {}
    ppr = fmt.get("ppr")
    try:
        ppr_out = float(ppr) if ppr is not None else 0.0
    except (TypeError, ValueError):
        ppr_out = 0.0
    return {
        "is_dynasty": bool(fmt.get("isDynasty") or fmt.get("is_dynasty")),
        "num_qbs": int(fmt.get("numQbs") or fmt.get("num_qbs") or 1),
        "ppr": ppr_out,
        "num_teams": int(fmt.get("numTeams") or fmt.get("num_teams") or 12),
    }


def as_of_block(*, season: str | int | None, week: int | None, generated_at: str | None = None) -> dict[str, Any]:
    return {
        "season": str(season) if season is not None else None,
        "week": int(week) if week is not None else None,
        "generated_at": generated_at or utc_now_iso(),
    }


def subject_block(
    *,
    team_name: str | None = None,
    manager: str | None = None,
    roster_id: Any = None,
) -> dict[str, Any]:
    return {
        "team_name": team_name,
        "manager": manager,
        "roster_id": roster_id,
    }


def advice_envelope(
    *,
    league_id: str | None,
    platform: str,
    fmt: dict[str, Any] | None,
    season: str | int | None,
    week: int | None,
    subject: dict[str, Any] | None,
    verdict: str,
    reasons: list[str] | None = None,
    data_sources: list[str] | None = None,
    limitations: list[str] | None = None,
    recommendations: list | None = None,
    unofficial: bool = True,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the common outer object. `extra` is merged last so a tool can
    override a default only when it means to."""
    out: dict[str, Any] = {
        "league_id": league_id,
        "platform": platform,
        "format": format_block(fmt),
        "as_of": as_of_block(season=season, week=week),
        "subject": subject,
        "verdict": verdict,
        "recommendations": list(recommendations or []),
        "reasons": list(reasons or []),
        "data_sources": list(data_sources or []),
        "limitations": list(limitations or []),
        "unofficial": bool(unofficial),
    }
    if extra:
        out.update(extra)
    return out
