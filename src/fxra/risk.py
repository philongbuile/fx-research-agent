"""Deterministic, fail-closed pre-trade risk gate.

Rules:
- Same inputs (intent, limits, snapshots, now, seen ids, kill switch) give the same decision.
- Anything missing, stale, malformed or unexpected is a rejection, never a pass.
- The gate collects every failing check so a reviewer sees all reasons at once.
- limits.mode must be "dry_run" or "demo" (D-30). "live" is always rejected.
"""

import math
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .contracts import AccountSnapshot, OrderIntent, Quote, SymbolSpec

ALLOWED_MODES = {"dry_run", "demo"}  # "demo" added by owner decision D-30; never "live" without a new decision


@dataclass(frozen=True)
class RiskLimits:
    mode: str
    allowed_symbols: frozenset[str]
    approved_strategies: frozenset[str]  # "strategy_id@version"
    max_lots_per_order: float
    max_open_positions: int
    max_daily_loss_fraction: float  # of day_start_equity, e.g. 0.02
    max_spread_points: int
    max_quote_age_seconds: float
    max_account_age_seconds: float
    require_stop_loss: bool
    kill_switch_file: str
    # Points differ by instrument (gold point = 0.01 USD); a symbol listed here overrides max_spread_points.
    max_spread_points_by_symbol: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "RiskLimits":
        raw = tomllib.loads(Path(path).read_text())["risk"]
        return cls(
            mode=str(raw["mode"]),
            allowed_symbols=frozenset(raw["allowed_symbols"]),
            approved_strategies=frozenset(raw["approved_strategies"]),
            max_lots_per_order=float(raw["max_lots_per_order"]),
            max_open_positions=int(raw["max_open_positions"]),
            max_daily_loss_fraction=float(raw["max_daily_loss_fraction"]),
            max_spread_points=int(raw["max_spread_points"]),
            max_quote_age_seconds=float(raw["max_quote_age_seconds"]),
            max_account_age_seconds=float(raw["max_account_age_seconds"]),
            require_stop_loss=bool(raw["require_stop_loss"]),
            kill_switch_file=str(raw["kill_switch_file"]),
            max_spread_points_by_symbol={str(k): int(v) for k, v in raw.get("max_spread_points_by_symbol", {}).items()},
        )


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reasons: tuple[str, ...]
    client_order_id: str
    checked_at: str

    def to_dict(self) -> dict:
        return asdict(self)


def _finite_pos(x) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x) and x > 0


def evaluate(intent: OrderIntent | None, limits: RiskLimits | None, spec: SymbolSpec | None,
             quote: Quote | None, account: AccountSnapshot | None, now: datetime,
             seen_client_order_ids: set[str], kill_switch_active: bool) -> RiskDecision:
    oid = getattr(intent, "client_order_id", "") or ""
    try:
        reasons = _check(intent, limits, spec, quote, account, now, seen_client_order_ids, kill_switch_active)
    except Exception as e:  # fail closed on any bug or bad input
        reasons = [f"gate_error:{type(e).__name__}:{e}"]
    return RiskDecision(approved=not reasons, reasons=tuple(reasons), client_order_id=oid,
                        checked_at=now.isoformat())


def _check(intent, limits, spec, quote, account, now, seen, kill_switch_active) -> list[str]:
    if now.tzinfo is None:
        return ["now_not_timezone_aware"]
    missing = [n for n, v in (("intent", intent), ("limits", limits), ("symbol_spec", spec),
                              ("quote", quote), ("account", account)) if v is None]
    if missing:
        return [f"missing:{m}" for m in missing]

    r: list[str] = []
    if kill_switch_active:
        r.append("kill_switch_active")
    if limits.mode not in ALLOWED_MODES:
        r.append(f"mode_not_allowed:{limits.mode}")

    # identity and approval
    if not intent.client_order_id:
        r.append("client_order_id_empty")
    elif intent.client_order_id in seen:
        r.append("duplicate_client_order_id")
    if f"{intent.strategy_id}@{intent.strategy_version}" not in limits.approved_strategies:
        r.append("strategy_version_not_approved")
    if intent.symbol not in limits.allowed_symbols:
        r.append("symbol_not_allowed")
    if spec.symbol != intent.symbol or quote.symbol != intent.symbol:
        r.append("symbol_mismatch_between_inputs")

    # order shape
    if intent.side not in ("buy", "sell"):
        r.append(f"bad_side:{intent.side}")
    if intent.order_type not in ("market", "limit", "stop"):
        r.append(f"bad_order_type:{intent.order_type}")
    if intent.order_type in ("limit", "stop") and not _finite_pos(intent.limit_price):
        r.append("limit_price_required")

    # size
    q = intent.quantity_lots
    if not _finite_pos(q):
        r.append("quantity_not_positive")
    else:
        if q < spec.volume_min or q > spec.volume_max:
            r.append("quantity_outside_broker_range")
        steps = q / spec.volume_step
        if abs(steps - round(steps)) > 1e-9:
            r.append("quantity_not_multiple_of_step")
        if q > limits.max_lots_per_order:
            r.append("quantity_above_limit")

    # market data
    if not (_finite_pos(quote.bid) and _finite_pos(quote.ask)) or quote.ask < quote.bid:
        r.append("quote_invalid")
    else:
        spread_points = (quote.ask - quote.bid) / spec.point
        max_spread = limits.max_spread_points_by_symbol.get(intent.symbol, limits.max_spread_points)
        if spread_points > max_spread + 1e-9:
            r.append(f"spread_too_wide:{spread_points:.1f}")
    if quote.time.tzinfo is None or (now - quote.time).total_seconds() > limits.max_quote_age_seconds:
        r.append("quote_stale_or_naive")
    if (now - quote.time).total_seconds() < -1:
        r.append("quote_from_future")

    # stop loss on the correct side of the entry
    if limits.require_stop_loss:
        sl = intent.stop_loss
        if not _finite_pos(sl):
            r.append("stop_loss_required")
        elif _finite_pos(quote.bid) and _finite_pos(quote.ask):
            entry = intent.limit_price if intent.order_type != "market" else (quote.ask if intent.side == "buy" else quote.bid)
            if (intent.side == "buy" and sl >= entry) or (intent.side == "sell" and sl <= entry):
                r.append("stop_loss_wrong_side")

    # account
    if account.time.tzinfo is None or (now - account.time).total_seconds() > limits.max_account_age_seconds:
        r.append("account_stale_or_naive")
    if not _finite_pos(account.day_start_equity) or not math.isfinite(account.equity):
        r.append("account_equity_invalid")
    elif (account.day_start_equity - account.equity) >= limits.max_daily_loss_fraction * account.day_start_equity:
        r.append("daily_loss_limit_reached")
    if account.open_positions >= limits.max_open_positions:
        r.append("max_open_positions_reached")
    return r


def kill_switch_active(limits: RiskLimits | None) -> bool:
    # Missing limits counts as active: no config, no trading.
    return limits is None or Path(limits.kill_switch_file).exists()
