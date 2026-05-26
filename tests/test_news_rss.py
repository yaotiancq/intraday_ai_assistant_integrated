from app.data_sources.news_rss_client import extract_tickers, classify_event, infer_sentiment


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
