from app.data_sources.futu_client import FutuQuoteClient, normalize_us_symbol, strip_prefix, MockQuoteClient


def test_symbol_normalization():
    assert normalize_us_symbol('AAPL') == 'US.AAPL'
    assert normalize_us_symbol('US.AAPL') == 'US.AAPL'
    assert strip_prefix('US.AAPL') == 'AAPL'


def test_mock_quote_client():
    c = MockQuoteClient(seed=1)
    rows = c.get_market_snapshot(['SPY', 'NVDA'])
    assert len(rows) == 2
    assert rows[0]['symbol'] == 'SPY'
    df = c.request_history_kline('SPY')
    assert not df.empty


def test_futu_quote_client_session_honors_extended_time_flag():
    class FakeSession:
        ALL = 'ALL'
        RTH = 'RTH'

    class FakeFutu:
        Session = FakeSession

    client = object.__new__(FutuQuoteClient)
    client.futu = FakeFutu

    client.extended_time = True
    assert client._session() == 'ALL'

    client.extended_time = False
    assert client._session() == 'RTH'
