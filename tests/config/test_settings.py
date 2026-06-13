import pytest

from trading_platform.config import Settings
from trading_platform.config.settings import DEFAULT_WATCHLIST


class TestSettings:
    def test_defaults(self):
        s = Settings()
        assert s.watchlist == DEFAULT_WATCHLIST
        assert s.scan_interval_minutes == 15
        assert s.strategy_params["min_score_to_alert"] == 2

    def test_rejects_nonpositive_interval(self):
        with pytest.raises(ValueError):
            Settings(scan_interval_minutes=0)

    def test_rejects_empty_watchlist(self):
        with pytest.raises(ValueError):
            Settings(watchlist=())

    def test_from_env_parses_watchlist(self, monkeypatch):
        monkeypatch.setenv("WATCHLIST", "aapl, msft ,NVDA")
        monkeypatch.setenv("SCAN_INTERVAL_MINUTES", "5")
        s = Settings.from_env()
        assert s.watchlist == ("AAPL", "MSFT", "NVDA")
        assert s.scan_interval_minutes == 5

    def test_from_env_empty_watchlist_falls_back_to_default(self, monkeypatch):
        monkeypatch.delenv("WATCHLIST", raising=False)
        assert Settings.from_env().watchlist == DEFAULT_WATCHLIST

    def test_strategy_params_not_shared_between_instances(self):
        a, b = Settings(), Settings()
        a.strategy_params["momentum_rsi_min"] = 99
        assert b.strategy_params["momentum_rsi_min"] == 60
