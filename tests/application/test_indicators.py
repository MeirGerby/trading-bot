import pytest

from trading_platform.application.indicators import rsi, sma


class TestSma:
    def test_basic(self):
        assert sma([1, 2, 3, 4], 2) == 3.5

    def test_uses_tail(self):
        assert sma([100, 1, 2, 3], 3) == 2.0

    @pytest.mark.parametrize("values,period", [([1, 2], 3), ([], 1), ([1], 0)])
    def test_insufficient_data_returns_none(self, values, period):
        assert sma(values, period) is None


class TestRsi:
    def test_all_gains_is_100(self):
        closes = list(range(1, 20))
        assert rsi(closes) == 100.0

    def test_all_losses_near_zero(self):
        closes = list(range(20, 1, -1))
        assert rsi(closes) < 1.0

    def test_alternating_is_midrange(self):
        closes = [100 + (1 if i % 2 else -1) for i in range(30)]
        value = rsi(closes)
        assert 40 <= value <= 60

    def test_insufficient_data_returns_none(self):
        assert rsi([1, 2, 3], period=14) is None

    def test_known_value(self):
        # Wilder's classic example data
        closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
                  45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
        assert rsi(closes, 14) == pytest.approx(70.46, abs=0.5)
