# TECHNICAL SMOKE TEST ONLY. NOT A STRATEGY. HAS NO EDGE.
# Purpose: prove the pinned LEAN image runs a Python algorithm, reads the forex
# sample data bundled in the image, fills an order, and writes results.
# EURUSD/Oanda/minute is used only because LEAN ships sample data for it; it is
# not a pair choice for this project (docs/decisions.md).
#
# The fxra runner passes parameters (get_parameter): start, end (YYYY-MM-DD),
# spread_multiplier, slippage_points. Defaults = the original hard-coded values.
from AlgorithmImports import *
from datetime import datetime


class TechnicalSmokeTest(QCAlgorithm):
    def initialize(self):
        # Image sample data has EURUSD minute quotes for 2014-05-01..2014-05-15 only.
        start = datetime.strptime(self.get_parameter("start", "2014-05-05"), "%Y-%m-%d")
        end = datetime.strptime(self.get_parameter("end", "2014-05-09"), "%Y-%m-%d")
        self.set_start_date(start.year, start.month, start.day)
        self.set_end_date(end.year, end.month, end.day)
        self.set_cash(100000)
        self.symbol = self.add_forex("EURUSD", Resolution.MINUTE, Market.OANDA).symbol
        self.spread_multiplier = float(self.get_parameter("spread_multiplier", "1"))
        self.slippage_points = float(self.get_parameter("slippage_points", "0"))
        if self.spread_multiplier != 1 or self.slippage_points != 0:
            self.securities[self.symbol].set_slippage_model(
                CostStressSlippage(self.spread_multiplier, self.slippage_points))
        self.log(f"PARAMS start={start:%Y-%m-%d} end={end:%Y-%m-%d} "
                 f"spread_multiplier={self.spread_multiplier} slippage_points={self.slippage_points}")

    def on_data(self, data: Slice):
        if not self.portfolio.invested and data.contains_key(self.symbol):
            self.market_order(self.symbol, 1000)

    def on_end_of_algorithm(self):
        self.log(f"SMOKE_TEST_DONE orders={self.transactions.orders_count} invested={self.portfolio.invested}")


class CostStressSlippage:
    """Extra cost per unit: (m - 1) x half the quoted spread, plus a fixed number of price points.
    LEAN already fills at bid/ask (the base spread); this adds on top of it."""

    def __init__(self, spread_multiplier, slippage_points):
        self.m = spread_multiplier
        self.points = slippage_points

    def get_slippage_approximation(self, asset, order):
        half_spread = (asset.ask_price - asset.bid_price) / 2
        return max(0.0, (self.m - 1) * half_spread) + self.points * asset.symbol_properties.minimum_price_variation
