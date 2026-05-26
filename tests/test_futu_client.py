from app.data_sources.futu_client import normalize_us_symbol, strip_prefix, MockQuoteClient


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
