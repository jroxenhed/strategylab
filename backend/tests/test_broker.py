from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))


def test_order_request_defaults():
    from broker import OrderRequest
    req = OrderRequest(symbol="AAPL", qty=10, side="buy")
    assert req.time_in_force == "day"
    assert req.order_type == "market"
    assert req.stop_price is None
    assert req.account_id is None


def test_order_result_fields():
    from broker import OrderResult
    res = OrderResult(order_id="123", symbol="AAPL", qty=10, side="buy", status="filled",
                      filled_avg_price=150.0, filled_qty=10)
    assert res.filled_avg_price == 150.0
    assert res.status == "filled"


def test_broker_registry():
    from broker import (
        register_trading_provider, get_trading_provider,
        get_available_brokers, get_active_broker, set_active_broker,
        _trading_providers, OrderRequest, OrderResult,
    )
    import broker

    # Clear state
    _trading_providers.clear()

    class FakeProvider:
        def get_account(self): return {"equity": 100000}
        def get_positions(self): return []
        def get_orders(self, status="all", symbols=None, limit=50): return []
        def submit_order(self, order): return OrderResult("1", order.symbol, order.qty, order.side, "filled")
        def get_order(self, order_id): return OrderResult(order_id, "AAPL", 10, "buy", "filled")
        def cancel_order(self, order_id): pass
        def close_position(self, symbol): return OrderResult("2", symbol, 10, "sell", "filled")
        def close_all_positions(self): pass
        def cancel_all_orders(self): pass
        def get_latest_price(self, symbol): return 150.0

    register_trading_provider("fake", FakeProvider())
    assert "fake" in get_available_brokers()

    broker._active_broker = "fake"
    provider = get_trading_provider()
    assert provider.get_account() == {"equity": 100000}

    # Unknown broker raises
    from fastapi import HTTPException
    import pytest
    broker._active_broker = "nonexistent"
    with pytest.raises(HTTPException) as exc_info:
        get_trading_provider()
    assert exc_info.value.status_code == 503


def test_alpaca_provider_submit_order():
    """AlpacaTradingProvider.submit_order() translates OrderRequest to Alpaca SDK."""
    from broker import AlpacaTradingProvider, OrderRequest, OrderResult

    class FakeOrder:
        id = "order-123"
        symbol = "AAPL"
        qty = "10"
        side = type("S", (), {"value": "buy"})()
        status = type("S", (), {"value": "accepted"})()
        filled_avg_price = None
        filled_qty = None

    class FakeClient:
        def submit_order(self, req):
            return FakeOrder()

    provider = AlpacaTradingProvider(FakeClient(), FakeClient())
    result = provider.submit_order(OrderRequest(symbol="AAPL", qty=10, side="buy"))
    assert isinstance(result, OrderResult)
    assert result.order_id == "order-123"
    assert result.side == "buy"


def test_alpaca_provider_get_positions():
    from broker import AlpacaTradingProvider

    class FakePos:
        symbol = "AAPL"
        qty = "10"
        side = type("S", (), {"value": "long"})()
        avg_entry_price = "150.00"
        current_price = "155.00"
        market_value = "1550.00"
        unrealized_pl = "50.00"
        unrealized_plpc = "0.0333"

    class FakeClient:
        def get_all_positions(self):
            return [FakePos()]

    provider = AlpacaTradingProvider(FakeClient(), FakeClient())
    positions = provider.get_positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "AAPL"
    assert positions[0]["side"] == "long"
    assert positions[0]["qty"] == 10.0


def test_alpaca_provider_get_account():
    from broker import AlpacaTradingProvider

    class FakeAccount:
        equity = "100000.00"
        cash = "50000.00"
        buying_power = "200000.00"
        portfolio_value = "100000.00"
        daytrade_count = 0
        pattern_day_trader = False
        trading_blocked = False
        account_blocked = False

    class FakeClient:
        def get_account(self):
            return FakeAccount()

    provider = AlpacaTradingProvider(FakeClient(), FakeClient())
    account = provider.get_account()
    assert account["equity"] == 100000.0
    assert account["cash"] == 50000.0


def test_ibkr_provider_submit_market_order():
    """IBKRTradingProvider.submit_order() translates to ib_insync MarketOrder."""
    from broker import IBKRTradingProvider, OrderRequest, OrderResult
    from unittest.mock import MagicMock

    ib = MagicMock()
    trade = MagicMock()
    trade.order.orderId = 42
    trade.order.action = "BUY"
    trade.order.totalQuantity = 10
    trade.orderStatus.status = "Submitted"
    trade.orderStatus.avgFillPrice = 0.0
    trade.orderStatus.filled = 0.0
    ib.placeOrder.return_value = trade

    provider = IBKRTradingProvider(ib)
    result = provider.submit_order(OrderRequest(symbol="AAPL", qty=10, side="buy"))
    assert isinstance(result, OrderResult)
    assert result.order_id == "42"
    assert result.side == "buy"
    ib.placeOrder.assert_called_once()


def test_ibkr_provider_get_positions():
    from broker import IBKRTradingProvider
    from unittest.mock import MagicMock

    ib = MagicMock()
    pos = MagicMock()
    pos.contract.symbol = "AAPL"
    pos.position = 10.0  # positive = long
    pos.avgCost = 150.0
    pos.account = "DU12345"
    ib.positions.return_value = [pos]

    provider = IBKRTradingProvider(ib)
    positions = provider.get_positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "AAPL"
    assert positions[0]["side"] == "long"
    assert positions[0]["qty"] == 10.0
    assert positions[0]["avg_entry"] == 150.0


def test_ibkr_provider_get_account():
    from broker import IBKRTradingProvider
    from unittest.mock import MagicMock

    ib = MagicMock()
    av = lambda tag, val: MagicMock(tag=tag, value=str(val))
    ib.accountSummary.return_value = [
        av("NetLiquidation", 100000),
        av("TotalCashValue", 50000),
        av("BuyingPower", 200000),
    ]

    provider = IBKRTradingProvider(ib)
    account = provider.get_account()
    assert account["equity"] == 100000.0
    assert account["cash"] == 50000.0
    assert account["buying_power"] == 200000.0


def test_ibkr_provider_position_side_from_sign():
    """Negative qty = short, positive = long."""
    from broker import IBKRTradingProvider
    from unittest.mock import MagicMock

    ib = MagicMock()
    long_pos = MagicMock()
    long_pos.contract.symbol = "AAPL"
    long_pos.position = 10.0
    long_pos.avgCost = 150.0
    long_pos.account = "DU12345"
    short_pos = MagicMock()
    short_pos.contract.symbol = "TSLA"
    short_pos.position = -5.0
    short_pos.avgCost = 200.0
    short_pos.account = "DU12345"
    ib.positions.return_value = [long_pos, short_pos]

    provider = IBKRTradingProvider(ib)
    positions = provider.get_positions()
    assert positions[0]["side"] == "long"
    assert positions[1]["side"] == "short"
    assert positions[1]["qty"] == 5.0  # absolute value
