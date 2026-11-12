from __future__ import annotations

from app.strategy.engine import FinalWindowStrategy, datetime_to_epoch_ms

from .helpers import books_for, make_config, make_market


def evaluate(remaining_before_end: float, up: float, down: float, **config):
    market = make_market(remaining=remaining_before_end + 1000)
    end_ms = datetime_to_epoch_ms(market.window_end)
    now_ms = end_ms - int(remaining_before_end * 1000)
    books = books_for(market, up, down)
    return FinalWindowStrategy().evaluate(market, books, now_ms, make_config(**config))


def test_first_four_minutes_never_trade():
    result = evaluate(240, up=0.95, down=0.95)
    assert result.in_window is False
    assert result.signals == []
    assert result.reason == "outside_final_window"


def test_final_60_seconds_activates_strategy():
    result = evaluate(60, up=0.50, down=0.50)
    assert result.in_window is True
    assert result.reason == "monitoring"


def test_up_at_079_does_not_trigger():
    result = evaluate(30, up=0.79, down=0.20)
    assert result.in_window is True
    assert all(side.side != "UP" for side in result.signals)
    assert result.up_armed is False


def test_down_at_079_does_not_trigger():
    result = evaluate(30, up=0.20, down=0.79)
    assert all(side.side != "DOWN" for side in result.signals)
    assert result.down_armed is False


def test_up_at_080_triggers():
    result = evaluate(30, up=0.80, down=0.20)
    assert [s.side for s in result.signals] == ["UP"]
    assert result.signals[0].best_ask == 0.80
    assert result.up_armed is True


def test_down_at_080_triggers():
    result = evaluate(30, up=0.20, down=0.80)
    assert [s.side for s in result.signals] == ["DOWN"]
    assert result.signals[0].best_ask == 0.80


def test_up_above_080_triggers():
    result = evaluate(30, up=0.93, down=0.07)
    assert result.signals[0].side == "UP"
    assert result.signals[0].best_ask == 0.93


def test_down_above_080_triggers():
    result = evaluate(30, up=0.07, down=0.91)
    assert result.signals[0].side == "DOWN"
    assert result.signals[0].best_ask == 0.91


def test_both_sides_can_trigger():
    result = evaluate(20, up=0.88, down=0.84)
    assert sorted(s.side for s in result.signals) == ["DOWN", "UP"]


def test_asks_visible_outside_window_for_dashboard():
    result = evaluate(180, up=0.42, down=0.61)
    assert result.in_window is False
    assert result.up_ask == 0.42
    assert result.down_ask == 0.61


def test_expired_market_never_triggers():
    result = evaluate(-1, up=0.95, down=0.95)
    assert result.in_window is False
    assert result.signals == []
    assert result.reason == "market_expired"


def test_threshold_is_configurable():
    result = evaluate(30, up=0.70, down=0.10, trigger_threshold=0.70)
    assert result.signals[0].side == "UP"


def test_final_window_is_configurable():
    result = evaluate(90, up=0.95, down=0.95, final_window_seconds=120)
    assert result.in_window is True
    assert len(result.signals) == 2
