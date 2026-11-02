import copy
import pytest

from bot import runner
from bot.backtest import compare, run_backtest
from bot.brokers import PaperLocalBroker
from bot.config import load_config, validate
from bot.data import synthetic_prices
from bot.risk import RiskError, apply_limits, check_can_trade
from bot.strategy import plan_orders, target_weights


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def prices(cfg):
    return synthetic_prices(list(cfg["portfolio"]) + [cfg["defensive_asset"]], days=800)


def test_config_rejects_bad_weights(cfg):
    bad = copy.deepcopy(cfg)
    bad["portfolio"]["VT"] = 0.9
    with pytest.raises(ValueError):
        validate(bad)


def test_trend_filter_moves_weight_to_defensive(cfg, prices):
    p = prices.copy()
    p.iloc[-1, p.columns.get_loc("QQQ")] = 1.0  # desplome: muy por debajo de la media
    w = target_weights(cfg, p)
    assert w["QQQ"] == 0 and w["BIL"] >= cfg["portfolio"]["QQQ"]
    assert sum(w.values()) == pytest.approx(1.0)


def test_contribution_only_buys_underweight():
    orders = plan_orders({"A": 70, "B": 30}, cash=100, targets={"A": 0.5, "B": 0.5}, rebalance_threshold=0.5)
    assert all(v > 0 for v in orders.values())          # sin ventas
    assert sum(orders.values()) == pytest.approx(100)
    assert orders["B"] > orders.get("A", 0)


def test_big_drift_triggers_full_rebalance():
    orders = plan_orders({"A": 90, "B": 10}, cash=0, targets={"A": 0.5, "B": 0.5}, rebalance_threshold=0.1)
    assert orders == {"A": -40, "B": 40}


def test_limits_cap_and_budget(cfg):
    out, notes = apply_limits(cfg, {"VT": 2000, "IEF": 3}, cash=2000)
    assert out == {"VT": 500}
    assert notes


def test_kill_switch(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr("bot.risk.ROOT", tmp_path)
    (tmp_path / "STOP").write_text("")
    with pytest.raises(RiskError):
        check_can_trade(cfg)


def test_live_requires_confirmation(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr("bot.risk.ROOT", tmp_path)
    monkeypatch.delenv("BOT_LIVE_CONFIRM", raising=False)
    live = dict(cfg, mode="alpaca_live")
    with pytest.raises(RiskError):
        check_can_trade(live)


def test_daily_loss_guard(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr("bot.risk.ROOT", tmp_path)
    with pytest.raises(RiskError):
        check_can_trade(cfg, equity_today=90, equity_yesterday=100)


def test_backtest_accounting(cfg, prices):
    r = run_backtest(cfg, prices)
    months = len({(d.year, d.month) for d in prices.index[cfg["strategy"]["sma_days"]:]})
    assert r.contributed.iloc[-1] == pytest.approx(months * cfg["contribution"]["monthly_usd"])
    assert r.equity.iloc[-1] > 0
    assert set(compare(cfg, prices).index) == {"con filtro de tendencia", "sin filtro (comprar y rebalancear)"}


def test_paper_run_end_to_end(cfg, prices, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "STATE", tmp_path / "runner.json")
    monkeypatch.setattr(runner, "LOG", tmp_path / "trades.csv")
    monkeypatch.setattr("bot.risk.ROOT", tmp_path)
    broker = PaperLocalBroker(prices.iloc[-1].to_dict(), 150, tmp_path / "acct.json")
    rep = runner.run_once(cfg, force=True, prices=prices, broker=broker)
    assert sum(rep["ordenes"].values()) == pytest.approx(150, abs=0.05)
    assert broker.cash() == pytest.approx(0, abs=0.05)
    assert (tmp_path / "trades.csv").exists()
    # Segunda ejecución del mismo mes: no opera
    rep2 = runner.run_once(cfg, prices=prices, broker=broker)
    assert rep2["ordenes"] == {}
