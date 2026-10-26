# TECHNICAL SMOKE TEST ONLY. NOT A STRATEGY. HAS NO EDGE.
# Purpose: prove the pinned LEAN image runs a Python algorithm, reads the forex
# sample data bundled in the image, fills an order, and writes results.
# EURUSD/Oanda/minute is used only because LEAN ships sample data for it; it is
# not a pair choice for this project (docs/decisions.md).
from AlgorithmImports import *


class TechnicalSmokeTest(QCAlgorithm):
    def initialize(self):
        # Image sample data has EURUSD minute quotes for 2014-05-01..2014-05-15 only.
        self.set_start_date(2014, 5, 5)
        self.set_end_date(2014, 5, 9)
        self.set_cash(100000)
        self.symbol = self.add_forex("EURUSD", Resolution.MINUTE, Market.OANDA).symbol

    def on_data(self, data: Slice):
        if not self.portfolio.invested and data.contains_key(self.symbol):
            self.market_order(self.symbol, 1000)

    def on_end_of_algorithm(self):
        self.log(f"SMOKE_TEST_DONE orders={self.transactions.orders_count} invested={self.portfolio.invested}")
