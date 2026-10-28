# TECHNICAL DATA REPLAY ONLY. No strategy, signals, order submissions or edge.
import json
from datetime import datetime, timedelta

from AlgorithmImports import *


class MT5DataReplay(QCAlgorithm):
    def initialize(self):
        self.set_time_zone("UTC")
        start = datetime.strptime(self.get_parameter("start"), "%Y-%m-%d")
        end = datetime.strptime(self.get_parameter("end"), "%Y-%m-%d")
        self.set_start_date(start.year, start.month, start.day)
        self.set_end_date(end.year, end.month, end.day)
        self.set_cash(100000)
        self.symbol = self.add_forex("EURUSD", Resolution.MINUTE, Market.OANDA, fill_forward=False).symbol
        with open("/Lean/Data/forex/oanda/replay-check.json") as f:
            validation = json.load(f)
        if not validation["technical_only"] or validation["source"] != "mt5-demo-1-observed":
            raise ValueError("wrong replay validation source")
        self.expected = {datetime.fromisoformat(r["time"]).replace(tzinfo=None): r
                         for r in validation["expected_bars"]}
        self.seen = set()

    def on_data(self, data: Slice):
        if not data.contains_key(self.symbol):
            return
        bar = data[self.symbol]
        stamp = bar.time
        if stamp not in self.expected or stamp in self.seen:
            raise ValueError("unexpected or duplicate replay bar")
        if bar.end_time != stamp + timedelta(minutes=1) or self.time < bar.end_time:
            raise ValueError("bar timing/look-ahead violation")
        expected = self.expected[stamp]
        for field in ("open", "high", "low", "close"):
            if abs(float(getattr(bar.bid, field)) - expected[field]) > 1e-9:
                raise ValueError("bid OHLC changed in replay")
        self.seen.add(stamp)
        self.set_runtime_statistic("Replay Bars", str(len(self.seen)))

    def on_end_of_algorithm(self):
        if not self.expected or self.seen != set(self.expected):
            raise ValueError("replay missing expected observations")
        if self.transactions.orders_count != 0:
            raise ValueError("data replay must never submit orders")
        self.set_runtime_statistic("Replay Verified", "true")
        self.log("MT5_DATA_REPLAY_VERIFIED")
