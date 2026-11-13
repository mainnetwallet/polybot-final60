from types import SimpleNamespace as NS

from app.services.order_outcomes import sell_outcomes


def _key_stage(k):
    import re
    m = re.search(r":s(\d+)", k or "")
    return int(m.group(1)) if m else 1


def _key_action(k):
    return "SELL" if ":sell:" in (k or "") else "BUY"


def _o(id, key, size, price, fee=0.0):
    return NS(id=id, order_key=key, market_id="m", side="DOWN", filled_size=size, avg_fill_price=price, fee=fee)


def test_stop_loss_sell_shows_loss():
    buy = _o(1, "paper:m:s0", 1.30, 0.77, 0.016)
    sell = _o(2, "paper:m:DOWN:s0:sell:ab", 1.29, 0.62, 0.021)
    res = sell_outcomes([buy, sell], _key_stage, _key_action)
    assert res[2][0] == "LOSS" and res[2][1] < 0


def test_take_profit_sell_shows_win():
    buy = _o(1, "paper:m", 1.10, 0.91, 0.006)
    sell = _o(2, "paper:m:DOWN:s1:sell:ab", 1.10, 0.99, 0.001)
    assert sell_outcomes([buy, sell], _key_stage, _key_action)[2][0] == "WIN"


def test_unfilled_sell_has_no_result():
    buy = _o(1, "paper:m", 1.0, 0.9)
    sell = _o(2, "paper:m:DOWN:s1:sell:ab", 0.0, None)
    assert sell_outcomes([buy, sell], _key_stage, _key_action) == {}
