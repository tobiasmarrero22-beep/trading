import copy

import pandas as pd
import pytest
import yaml

from bot.config import ROOT
from daytrade import backtest as bt
from daytrade.engine import Engine, check_live_allowed
from daytrade.risk import DayGuard, position_size


@pytest.fixture
def cfg():
    return yaml.safe_load(open(ROOT / "daytrade.yaml"))


class FakeExchange:
    def __init__(self, fee=0.001):
        self.fee, self.px = fee, 100.0

    def free_usd(self, symbol):
        return float("inf")

    def buy(self, symbol, usd):
        f = usd * self.fee
        return {"qty": (usd - f) / self.px, "price": self.px, "cost": usd, "fee": f, "id": "x"}

    def sell(self, symbol, qty):
        g = qty * self.px
        return {"price": self.px, "proceeds": g * (1 - self.fee), "fee": g * self.fee, "id": "y"}


def test_position_size_risks_one_percent(cfg):
    usd = position_size(200, 200, entry=100, stop=99, risk=cfg["risk"])  # stop a 1 %
    assert usd == pytest.approx(200)            # 2 USD de riesgo / 1 % = 200 USD
    usd = position_size(200, 200, entry=100, stop=90, risk=cfg["risk"])  # stop a 10 %
    assert usd == pytest.approx(20)
    assert position_size(200, 200, 100, 99.99, dict(cfg["risk"], max_position_pct=0.5)) == pytest.approx(100)
    assert position_size(5, 5, 100, 90, cfg["risk"]) == 0  # por debajo del mínimo


def test_day_guard_limits(cfg):
    g = DayGuard()
    g.roll("2025-01-01", 200)
    g.closed(-6.5)  # -3,25 %
    assert g.can_open(cfg["risk"])[0] is False
    g.roll("2025-01-02", 193.5)
    assert g.can_open(cfg["risk"])[0] is True
    g.closed(9.5)
    assert g.can_open(dict(cfg["risk"], daily_profit_target_usd=9))[0] is False


def test_backtest_runs_and_accounts_fees(cfg):
    df = bt.synthetic_candles(60)
    for s in ("reversion", "breakout"):
        r = bt.run(df, dict(cfg, strategy=s))
        if len(r.trades):
            assert r.trades.fees.gt(0).all()
            assert r.equity.iloc[-1] == pytest.approx(cfg["initial_usd"] + r.trades.pnl.sum(), abs=1e-6)
        assert r.daily.sum() == pytest.approx(r.trades.pnl.sum() if len(r.trades) else 0)


def test_backtest_stop_takes_priority():
    """Si una vela toca stop y objetivo a la vez, se asume que tocó el stop (supuesto pesimista)."""
    c = yaml.safe_load(open(ROOT / "daytrade.yaml"))
    c["params"].update(ema_trend=2, atr_period=2, rsi_period=2, breakout_bars=2)
    c["strategy"] = "breakout"
    idx = pd.date_range("2025-01-01", periods=8, freq="15min", tz="UTC")
    o = [100, 102, 104, 106, 108, 110, 112, 114]
    df = pd.DataFrame({"open": o, "high": [x + 1 for x in o], "low": [x - 1 for x in o], "close": o, "volume": 1}, index=idx)
    df.loc[idx[5], ["high", "low"]] = [200, 1]
    r = bt.run(df, c)
    assert "stop" in r.trades.motivo.iloc[0]


def _candles(n=300, last_close=None, trend=1.0):
    idx = pd.date_range("2025-01-01", periods=n, freq="15min", tz="UTC")
    close = pd.Series([100 + trend * i for i in range(n)], index=idx, dtype=float)
    if last_close is not None:
        close.iloc[-1] = last_close
    return pd.DataFrame({"open": close, "high": close + 0.5, "low": close - 0.5, "close": close, "volume": 1})


def test_engine_full_cycle(cfg, tmp_path):
    c = copy.deepcopy(cfg)
    c["strategy"] = "breakout"
    ex = FakeExchange()
    eng = Engine(c, ex, tmp_path / "s.json", tmp_path / "j.csv")
    candles = _candles()
    ex.px = candles.close.iloc[-1]
    ev = eng.step(candles, ex.px)
    assert eng.pos and ev[0].startswith("COMPRA")
    # El estado se guarda y se recupera
    eng2 = Engine(c, ex, tmp_path / "s.json", tmp_path / "j.csv")
    assert eng2.pos == eng.pos
    # Cae por debajo del stop -> vende
    ex.px = eng2.pos["stop"] - 1
    ev = eng2.step(candles, ex.px)
    assert eng2.pos is None and "stop" in ev[0]
    assert eng2.guard.pnl < 0
    assert (tmp_path / "j.csv").read_text().count("\n") == 2
    assert eng2.capital == pytest.approx(c["initial_usd"] + eng2.guard.pnl)


def test_engine_ignores_same_candle_twice(cfg, tmp_path):
    c = dict(cfg, strategy="breakout")
    ex = FakeExchange()
    eng = Engine(c, ex, tmp_path / "s.json", tmp_path / "j.csv")
    candles = _candles()
    ex.px = candles.close.iloc[-1]
    eng.step(candles, ex.px)
    eng.pos["tp"] = 1e9
    qty = eng.pos["qty"]
    assert eng.step(candles, ex.px) == [] and eng.pos["qty"] == qty


def test_live_needs_confirmation(cfg, monkeypatch):
    monkeypatch.delenv("DAYTRADE_LIVE_CONFIRM", raising=False)
    with pytest.raises(RuntimeError):
        check_live_allowed(dict(cfg, mode="binance_live"))


def test_momentum_signal_on_cross(cfg):
    from daytrade.strategies import add_indicators, entry_signal
    p = dict(cfg["params"], ema_trend=5, ema_fast=3, ema_slow=8)
    close = [100 - i for i in range(40)] + [60 + 3 * i for i in range(40)]
    idx = pd.date_range("2025-01-01", periods=len(close), freq="1h", tz="UTC")
    df = pd.DataFrame({"open": close, "high": [c + 1 for c in close], "low": [c - 1 for c in close],
                       "close": close, "volume": 1.0}, index=idx)
    ind = add_indicators(df, p)
    assert ind.cross_up.sum() == 1                        # un solo cruce al alza
    assert not entry_signal(ind.iloc[-1], "momentum", p)  # después del cruce ya no hay señal nueva


def test_out_of_sample_start(cfg):
    df = bt.synthetic_candles(60)
    split = df.index[len(df) // 2]
    r = bt.run(df, dict(cfg, strategy="breakout"), start_at=split)
    assert r.equity.index[0] == split
    if len(r.trades):
        assert (r.trades.entrada >= split).all()


def test_resample_and_research_smoke(cfg):
    from daytrade import research as rs
    h1 = bt.synthetic_candles(90, tf_minutes=60)
    h4 = rs.resample(h1, "4h")
    assert len(h4) == pytest.approx(len(h1) / 4, abs=1)
    assert h4.high.iloc[0] == h1.high.iloc[:4].max()
    res = rs.research(cfg, {("X/USDT", "4h"): h4})
    assert len(res) == sum(1 for _ in rs.combos())
    assert "candidata" in res.columns
    assert rs.verdict(res)


def test_analyze_reads_trend(cfg):
    from daytrade.analysis import analyze
    up = _candles(400, trend=1.0)
    r = analyze(up, "breakout", cfg["params"])
    assert r["tendencia"] == "ALCISTA" and "clara" in r["fuerza"]
    down = _candles(400, trend=-0.2)
    r = analyze(down, "reversion", cfg["params"])
    assert r["tendencia"] == "BAJISTA" and r["operaria_ahora"] == "NO" and "por debajo" in r["por_que"]
