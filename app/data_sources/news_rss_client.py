from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List, Set
import re
try:
    import feedparser  # type: ignore
except Exception:  # pragma: no cover - optional dependency fallback
    feedparser = None
import requests

COMMON_WORDS = {
    'A', 'I', 'AM', 'PM', 'CEO', 'CFO', 'IPO', 'ETF', 'SEC', 'USA', 'GDP', 'CPI', 'PCE',
    'FOMC', 'AI', 'US', 'EU', 'UK', 'API', 'EPS', 'FDA', 'DOJ', 'FTC', 'WSJ', 'CNN'
}

EVENT_KEYWORDS = {
    'earnings': ['earnings', 'revenue', 'profit', 'eps', 'guidance', 'quarter'],
    'rating_change': ['upgrade', 'downgrade', 'price target', 'initiates', 'analyst'],
    'mna': ['acquire', 'acquisition', 'merger', 'takeover', 'buyout'],
    'regulatory': ['sec', 'doj', 'ftc', 'lawsuit', 'probe', 'investigation', 'regulator'],
    'macro': ['cpi', 'pce', 'fed', 'fomc', 'jobs report', 'nonfarm', 'treasury yield'],
    'product': ['launch', 'product', 'partnership', 'contract', 'order'],
    'rumor': ['rumor', 'reportedly', 'sources say', 'talks', 'speculation'],
}


@dataclass
class NewsRSSClient:
    urls: List[str]
    timeout: int = 8
    max_workers: int = 8

    def fetch(self, allowed_symbols: Iterable[str] | None = None) -> List[Dict[str, Any]]:
        allowed = {s.upper() for s in allowed_symbols} if allowed_symbols else set()
        urls = [url.strip() for url in self.urls if url and url.strip()]
        if not urls:
            return []

        if len(urls) == 1:
            return self._fetch_url(urls[0], allowed)

        items: List[Dict[str, Any]] = []
        workers = max(1, min(self.max_workers, len(urls)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(self._fetch_url, url, allowed) for url in urls]
            for future in as_completed(futures):
                items.extend(future.result())
        return items

    def _fetch_url(self, url: str, allowed: Set[str]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        try:
            raw = requests.get(url, timeout=self.timeout, headers={'User-Agent': 'intraday-ai-assistant/1.0'})
            raw.raise_for_status()
            if feedparser is None:
                # Minimal fallback: dependency not installed; preserve warning instead of crashing.
                raise RuntimeError('feedparser is not installed; run pip install -r requirements.txt')
            feed = feedparser.parse(raw.content)
            for entry in feed.entries:
                item = self._entry_to_item(entry, url, allowed)
                if item:
                    items.append(item)
        except Exception as exc:
            items.append({
                'title': 'RSS fetch failed',
                'source': url,
                'url': url,
                'published_at': None,
                'retrieved_at': datetime.now(timezone.utc).isoformat(),
                'summary': str(exc),
                'related_symbols': [],
                'event_type': 'warning',
                'sentiment': 'neutral',
                'credibility': 'rss_source',
                'confidence': 'low',
                'news_score': 0,
                'is_error': True,
            })
        return items

    def _entry_to_item(self, entry: Any, source_url: str, allowed: Set[str]) -> Dict[str, Any] | None:
        title = getattr(entry, 'title', '') or ''
        summary = getattr(entry, 'summary', '') or getattr(entry, 'description', '') or ''
        link = getattr(entry, 'link', '') or source_url
        text = f'{title} {summary}'
        related = extract_tickers(text, allowed)
        if allowed and not related:
            return None
        event_type = classify_event(text)
        return {
            'title': clean_html(title),
            'source': source_url,
            'url': link,
            'published_at': parse_entry_time(entry),
            'retrieved_at': datetime.now(timezone.utc).isoformat(),
            'summary': clean_html(summary)[:800],
            'related_symbols': sorted(related),
            'event_type': event_type,
            'sentiment': infer_sentiment(text),
            'credibility': 'rss_source',
            'confidence': confidence_for_event(event_type),
            'news_score': score_news(event_type, text),
            'is_error': False,
        }


SYMBOL_ALIASES = {
    # Mega-cap / AI / semis
    'NVDA': ['nvidia'],
    'AMD': ['advanced micro devices'],
    'AVGO': ['broadcom'],
    'QCOM': ['qualcomm'],
    'INTC': ['intel'],
    'ARM': ['arm holdings'],
    'MU': ['micron', 'micron technology'],
    'TSM': ['taiwan semiconductor', 'tsmc'],
    'ASML': ['asml'],
    'MSFT': ['microsoft'],
    'AAPL': ['apple'],
    'AMZN': ['amazon'],
    'GOOGL': ['alphabet', 'google'],
    'META': ['meta platforms', 'facebook'],
    'TSLA': ['tesla'],
    # Software / cloud
    'CRM': ['salesforce'],
    'ORCL': ['oracle'],
    'NOW': ['servicenow'],
    'PLTR': ['palantir'],
    'SNOW': ['snowflake'],
    # Energy / defensives / retail
    'XOM': ['exxon', 'exxon mobil'],
    'CVX': ['chevron'],
    'COP': ['conocophillips'],
    'SLB': ['schlumberger'],
    'JNJ': ['johnson & johnson', 'johnson and johnson'],
    'PEP': ['pepsico'],
    'KO': ['coca-cola', 'coca cola'],
    'COST': ['costco'],
    'WMT': ['walmart'],
    # ETFs / index proxies
    'SPY': ['s&p 500', 's&p500'],
    'QQQ': ['nasdaq 100', 'nasdaq-100'],
    'IWM': ['russell 2000'],
    'DIA': ['dow jones', 'dow industrials'],
    'SMH': ['semiconductor etf'],
    'SOXX': ['semiconductor etf'],
    'XLE': ['energy sector'],
    'XLF': ['financial sector'],
    'XLK': ['technology sector'],
}


def extract_tickers(text: str, allowed: Set[str] | None = None) -> Set[str]:
    allowed = {s.upper() for s in allowed} if allowed else set()
    tickers: Set[str] = set()
    upper_text = text.upper()
    lower_text = text.lower()

    # Match explicit symbols like $NVDA or (NASDAQ: NVDA).
    for m in re.finditer(r'\$([A-Z]{1,5})\b|\b(?:NASDAQ|NYSE|AMEX):\s*([A-Z]{1,5})\b', upper_text):
        sym = (m.group(1) or m.group(2) or '').upper()
        if sym and sym not in COMMON_WORDS and (not allowed or sym in allowed):
            tickers.add(sym)

    # Match known allowed symbols as standalone words.
    if allowed:
        for sym in allowed:
            if re.search(rf'(?<![A-Z0-9]){re.escape(sym)}(?![A-Z0-9])', upper_text):
                tickers.add(sym)

            # Also match company names / common aliases, because RSS headlines often say
            # "Nvidia", "Apple", "Broadcom", etc. instead of ticker symbols.
            for alias in SYMBOL_ALIASES.get(sym, []):
                if re.search(rf'(?<![a-z0-9]){re.escape(alias.lower())}(?![a-z0-9])', lower_text):
                    tickers.add(sym)

    return tickers


def classify_event(text: str) -> str:
    low = text.lower()
    for event, keys in EVENT_KEYWORDS.items():
        if any(k in low for k in keys):
            return event
    return 'general_news'


def infer_sentiment(text: str) -> str:
    low = text.lower()
    positive = ['beats', 'surges', 'jumps', 'rises', 'raises', 'upgrade', 'wins', 'strong']
    negative = ['misses', 'falls', 'drops', 'cuts', 'downgrade', 'lawsuit', 'probe', 'weak']
    p = sum(k in low for k in positive)
    n = sum(k in low for k in negative)
    if p > n:
        return 'positive'
    if n > p:
        return 'negative'
    return 'neutral'


def confidence_for_event(event_type: str) -> str:
    if event_type in {'earnings', 'mna', 'regulatory'}:
        return 'medium'
    if event_type == 'rumor':
        return 'low'
    return 'medium'


def score_news(event_type: str, text: str) -> int:
    base = {
        'earnings': 25,
        'mna': 25,
        'regulatory': 22,
        'rating_change': 14,
        'product': 16,
        'macro': 12,
        'rumor': 10,
        'general_news': 6,
        'warning': 0,
    }.get(event_type, 5)
    low = text.lower()
    if any(x in low for x in ['breaking', 'exclusive', 'urgent']):
        base += 8
    if any(x in low for x in ['reportedly', 'rumor', 'speculation']):
        base -= 4
    return max(0, min(30, base))


def clean_html(text: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', text or '')
    return re.sub(r'\s+', ' ', text).strip()


def parse_entry_time(entry: Any) -> str | None:
    for attr in ['published_parsed', 'updated_parsed']:
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc).isoformat()
            except Exception:
                pass
    for attr in ['published', 'updated']:
        val = getattr(entry, attr, None)
        if val:
            return str(val)
    return None
