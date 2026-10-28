"""Regression for LEAN's exchange-local bars and aware/naive UTC callback clocks."""
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.mark.parametrize("aware", [False, True])
def test_exchange_label_and_callback_clock_are_compared_in_utc(monkeypatch, aware):
    imports = ModuleType("AlgorithmImports")
    imports.QCAlgorithm = object
    imports.Slice = dict
    noda = ModuleType("NodaTime")
    noda.DateTimeZone = noda.Offset = object
    monkeypatch.setitem(sys.modules, "AlgorithmImports", imports)
    monkeypatch.setitem(sys.modules, "NodaTime", noda)
    path = Path(__file__).parents[1] / "lean/MT5DataReplay/main.py"
    spec = importlib.util.spec_from_file_location("mt5_replay_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    algo = module.MT5DataReplay()
    algo.symbol = "EURUSD"
    algo.securities = {"EURUSD": SimpleNamespace(exchange=SimpleNamespace(
        time_zone=SimpleNamespace(Id="America/New_York")))}
    algo.expected = {datetime(2026, 10, 28, 10, 35): {"open": 1.1, "high": 1.2, "low": 1., "close": 1.15}}
    algo.seen = set()
    algo.utc_time = datetime(2026, 10, 28, 10, 36, tzinfo=timezone.utc if aware else None)
    stats = {}
    algo.set_runtime_statistic = lambda key, value: stats.update({key: value})
    bar = SimpleNamespace(time=datetime(2026, 10, 28, 6, 35), end_time=datetime(2026, 10, 28, 6, 36),
                          bid=SimpleNamespace(open=1.1, high=1.2, low=1., close=1.15))

    class Slice(dict):
        def contains_key(self, key):
            return key in self

    algo.on_data(Slice(EURUSD=bar))
    assert algo.seen == set(algo.expected) and stats["Replay Bars"] == "1"
    assert bar.time.hour == 6  # Source object was not rewritten.
    with pytest.raises(ValueError, match="duplicate"):
        algo.on_data(Slice(EURUSD=bar))
