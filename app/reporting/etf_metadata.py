from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ETFMetadata:
    symbol: str
    category: str
    display_name_zh: str
    display_name_en: str | None = None

    @property
    def is_unknown(self) -> bool:
        return self.category == 'unknown'


ETF_METADATA: dict[str, ETFMetadata] = {
    'SPY': ETFMetadata('SPY', 'broad_market', '标普500', 'S&P 500'),
    'QQQ': ETFMetadata('QQQ', 'broad_market', '纳斯达克100', 'Nasdaq 100'),
    'IWM': ETFMetadata('IWM', 'style', '小盘股', 'Russell 2000'),
    'DIA': ETFMetadata('DIA', 'broad_market', '道琼斯工业指数', 'Dow Jones Industrial Average'),
    'XLK': ETFMetadata('XLK', 'sector', '信息技术', 'Technology Select Sector'),
    'XLC': ETFMetadata('XLC', 'sector', '通信服务', 'Communication Services Select Sector'),
    'XLY': ETFMetadata('XLY', 'sector', '非必需消费', 'Consumer Discretionary Select Sector'),
    'XLP': ETFMetadata('XLP', 'sector', '必需消费', 'Consumer Staples Select Sector'),
    'XLF': ETFMetadata('XLF', 'sector', '金融', 'Financial Select Sector'),
    'XLE': ETFMetadata('XLE', 'sector', '能源', 'Energy Select Sector'),
    'XLV': ETFMetadata('XLV', 'sector', '医疗保健', 'Health Care Select Sector'),
    'XLU': ETFMetadata('XLU', 'sector', '公用事业', 'Utilities Select Sector'),
    'XLI': ETFMetadata('XLI', 'sector', '工业', 'Industrial Select Sector'),
    'XLB': ETFMetadata('XLB', 'sector', '原材料', 'Materials Select Sector'),
    'XLRE': ETFMetadata('XLRE', 'sector', '房地产', 'Real Estate Select Sector'),
    'SMH': ETFMetadata('SMH', 'industry', '半导体', 'VanEck Semiconductor ETF'),
    'SOXX': ETFMetadata('SOXX', 'industry', '半导体', 'iShares Semiconductor ETF'),
    'IGV': ETFMetadata('IGV', 'industry', '软件', 'iShares Expanded Tech-Software Sector ETF'),
    'KRE': ETFMetadata('KRE', 'industry', '区域银行', 'SPDR S&P Regional Banking ETF'),
    'XBI': ETFMetadata('XBI', 'industry', '生物科技', 'SPDR S&P Biotech ETF'),
    'XRT': ETFMetadata('XRT', 'industry', '零售', 'SPDR S&P Retail ETF'),
}


def get_etf_metadata(symbol: str) -> ETFMetadata:
    normalized = str(symbol or '').strip().upper()
    if not normalized:
        normalized = 'UNKNOWN'
    return ETF_METADATA.get(
        normalized,
        ETFMetadata(normalized, 'unknown', '行业未知', None),
    )


def format_etf_display_name(symbol: str) -> str:
    metadata = get_etf_metadata(symbol)
    return f'{metadata.symbol}（{metadata.display_name_zh}）'


def collect_unknown_etfs(symbols: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    return sorted(
        get_etf_metadata(symbol).symbol
        for symbol in symbols
        if get_etf_metadata(symbol).is_unknown
    )
