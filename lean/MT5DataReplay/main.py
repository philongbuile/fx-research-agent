# TECHNICAL DATA REPLAY ONLY. No strategy, signals, order submissions or edge.
import json
from datetime import datetime, timedelta, timezone

from AlgorithmImports import *
from NodaTime import DateTimeZone, Offset


class MT5DataReplay(QCAlgorithm):
    def initialize(self):
        # A fixed-offset TECHNICAL algorithm calendar, distinct from UTC data time.
        # LEAN clamps its end date to yesterday in THIS calendar. Use only a
        # previously observed window that has closed in this calendar; no source
        # timestamps or engine/system clocks are changed.
        self.set_time_zone(DateTimeZone.ForOffset(Offset.FromHoursAndMinutes(13, 15)))
        start = datetime.strptime(self.get_parameter("start"), "%Y-%m-%d")
        end = datetime.strptime(self.get_parameter("end"), "%Y-%m-%d")
        with open("/Lean/Data/forex/oanda/replay-check.json") as f:
            validation = json.load(f)
        if not validation["technical_only"] or validation["source"] != "mt5-demo-1-observed":
            raise ValueError("wrong replay validation source")
        self.expected = {datetime.fromisoformat(r["time"]).replace(tzinfo=None): r
                         for r in validation["expected_bars"]}
        if not self.expected or any(not start.date() <= t.date() <= end.date() for t in self.expected):
            raise ValueError("replay observations outside requested UTC data dates")
        calendar = timezone(timedelta(hours=13, minutes=15))
        local_start = min(self.expected).replace(tzinfo=timezone.utc).astimezone(calendar)
        local_end = (max(self.expected) + timedelta(minutes=1)).replace(tzinfo=timezone.utc).astimezone(calendar)
        self.set_start_date(local_start.year, local_start.month, local_start.day)
        self.set_end_date(local_end.year, local_end.month, local_end.day)
        if local_end.replace(tzinfo=None) > self.end_date:
            raise ValueError("observation is not within a closed algorithm calendar day")
        self.set_cash(100000)
        self.symbol = self.add_forex("EURUSD", Resolution.MINUTE, Market.OANDA, fill_forward=False).symbol
        self.seen = set()

    def on_data(self, data: Slice):
        if not data.contains_key(self.symbol):
            return
        bar = data[self.symbol]
        stamp = bar.time
        if stamp not in self.expected or stamp in self.seen:
            raise ValueError("unexpected or duplicate replay bar")
        if bar.end_time != stamp + timedelta(minutes=1) or self.utc_time < bar.end_time:
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
