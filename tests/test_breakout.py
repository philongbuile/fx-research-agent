from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pytest

from fxra.breakout import evaluate
from fxra.contracts import BARS_SCHEMA

START = datetime(2026, 1, 6, tzinfo=timezone.utc)
SIGNAL = START + timedelta(hours=24)
END = SIGNAL + timedelta(hours=1)


def data(close=1.1):
    rows = [{"time": START + timedelta(minutes=i), "open": 1.1,
             "high": 1.2, "low": 1.0, "close": 1.1,
             "tick_volume": 1, "spread_points": 10} for i in range(25 * 60)]
    rows[-1].update(close=close, high=max(1.2, close), low=min(1.0, close))
    return rows


def evaluate_rows(rows, **kwargs):
    return evaluate(pa.Table.from_pylist(rows, schema=BARS_SCHEMA),
                    signal_open=SIGNAL, available_at=kwargs.get("available_at", END))


@pytest.mark.parametrize("close,side", [(1.3, "buy"), (.9, "sell"), (1.2, None), (1.0, None)])
def test_channel_excludes_signal_bar_and_requires_strict_break(close, side):
    signal = evaluate_rows(data(close))
    assert signal.side == side
    assert signal.available_at == END
    assert signal.long_exit_below == 1.0
    assert signal.short_exit_above == 1.2
    expected_last = max(1.2, close) - min(1.0, close)
    assert signal.atr == pytest.approx((13 * .2 + expected_last) / 14)


def test_no_signal_on_unfinished_hour_or_future_input():
    with pytest.raises(ValueError, match="unfinished"):
        evaluate_rows(data(), available_at=END - timedelta(microseconds=1))
    rows = data()
    rows.append(dict(rows[-1], time=END))
    with pytest.raises(ValueError, match="outside"):
        evaluate_rows(rows)


def test_gap_in_warmup_suppresses_signal_and_exit_thresholds():
    rows = data(1.3)
    rows.pop(10)
    assert evaluate_rows(rows) is None


def test_atr_includes_previous_close_gap_and_exits_use_only_last_ten():
    rows = data()
    # An old extreme affects entry channel but not the 10-hour exit channel.
    rows[0]["high"] = 1.8
    for r in rows[24 * 60:]:
        r.update(open=1.5, high=1.6, low=1.4, close=1.5)
    signal = evaluate_rows(rows)
    assert signal.side is None
    assert signal.short_exit_above == 1.2
    assert signal.atr == pytest.approx((13 * .2 + .5) / 14)
