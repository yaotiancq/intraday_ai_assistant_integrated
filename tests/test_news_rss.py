from app.data_sources.news_rss_client import NewsRSSClient, extract_tickers, classify_event, infer_sentiment


def test_extract_tickers_allowed():
    text = 'Nvidia $NVDA rises while AMD announces new AI chip.'
    assert extract_tickers(text, {'NVDA', 'AMD'}) == {'NVDA', 'AMD'}


def test_classify_event():
    assert classify_event('Company reports earnings and raises guidance') == 'earnings'
    assert classify_event('Analyst upgrade with new price target') == 'rating_change'
    assert classify_event('Talks and speculation about merger') == 'mna'


def test_infer_sentiment():
    assert infer_sentiment('stock surges after strong earnings') == 'positive'
    assert infer_sentiment('stock drops after weak guidance') == 'negative'


def test_extract_tickers_company_aliases():
    text = 'Nvidia, Broadcom and Apple rise while Tesla falls.'
    assert extract_tickers(text, {'NVDA', 'AVGO', 'AAPL', 'TSLA'}) == {'NVDA', 'AVGO', 'AAPL', 'TSLA'}


def test_news_rss_client_returns_warning_item_on_fetch_error(monkeypatch):
    def fake_get(*args, **kwargs):
        raise RuntimeError('network unavailable')

    monkeypatch.setattr('app.data_sources.news_rss_client.requests.get', fake_get)

    items = NewsRSSClient(['https://example.invalid/rss']).fetch()
    assert len(items) == 1
    assert items[0]['is_error'] is True
    assert items[0]['event_type'] == 'warning'
