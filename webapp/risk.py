"""Deterministic entry/stop/target arithmetic from explicit agent report fields.

These levels are AI proposals, NOT independently verified prices or an order.
We do not manufacture a target or infer a target from support/resistance prose.
"""
from __future__ import annotations

import math
import re

from webapp.quality import _reported_field

_PRICE = re.compile(r"^(?:[₹$€£]\s*)?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)$")


def parse_price(value: str | None) -> float | None:
    if not isinstance(value, str):
        return None
    matched = _PRICE.fullmatch(value.strip())
    if not matched:
        return None
    price = float(matched.group(1).replace(",", ""))
    return price if math.isfinite(price) and price > 0 else None


def _direction(rating: str) -> str | None:
    if rating in {"Buy", "Overweight"}:
        return "long"
    if rating in {"Sell", "Underweight"}:
        return "short"
    return None


def proposal_risk(state: dict, rating: str) -> dict:
    """Risk/reward only when final direction and all explicit levels are coherent."""
    trader = str(state.get("trader_investment_plan", "") or "")
    decision = str(state.get("final_trade_decision", "") or "")
    entry = parse_price(_reported_field(trader, "Entry Price"))
    stop = parse_price(_reported_field(trader, "Stop Loss"))
    target = parse_price(_reported_field(decision, "Price Target"))
    direction = _direction(rating)
    trader_action = _reported_field(trader, "Action")
    warnings = []
    risk_pct = upside_pct = multiple = None

    action = trader_action.strip().lower() if trader_action else ""
    conflict = bool(direction and action in {"buy", "sell", "hold"} and (
        (direction == "long" and action != "buy")
        or (direction == "short" and action != "sell")
    ))
    if not direction:
        warnings.append("No directional final rating; risk/reward is not applicable.")
    elif conflict:
        warnings.append("Trader action conflicts with the final portfolio rating; risk/reward is withheld.")
    if entry is None:
        warnings.append("No valid numeric entry price provided by the Trader.")
    if stop is None:
        warnings.append("No valid numeric stop-loss provided by the Trader.")
    if target is None:
        warnings.append("No valid numeric price target provided by the Portfolio Manager.")

    if direction and not conflict and entry and stop:
        risk = (entry - stop) if direction == "long" else (stop - entry)
        if risk <= 0:
            warnings.append("Stop-loss is on the wrong side of entry for this direction.")
        else:
            risk_pct = round(100 * risk / entry, 2)
            if target:
                reward = (target - entry) if direction == "long" else (entry - target)
                if reward <= 0:
                    warnings.append("Price target is on the wrong side of entry for this direction.")
                else:
                    upside_pct = round(100 * reward / entry, 2)
                    multiple = round(reward / risk, 2)
    return {
        "direction": direction,
        "trader_action": trader_action,
        "entry": entry,
        "stop_loss": stop,
        "price_target": target,
        "risk_pct": risk_pct,
        "reward_pct": upside_pct,
        "reward_to_risk": multiple,
        "warnings": warnings,
        "disclaimer": "These figures use AI-suggested entry/stop/target prices only. They exclude slippage, gaps, fees, taxes, and probability of reaching a target; no order is placed.",
    }
