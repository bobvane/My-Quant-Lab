"""Symbol canonicalisation so external holdings match our market tickers.

Ghostfolio reports crypto by CoinGecko name (``BITCOIN``, ``ETHEREUM``,
``BINANCECOIN``) while our market data uses exchange tickers (``BTC-USD``,
``ETH-USD``, ``BNB-USD``). Reducing both sides to a canonical key lets the
portfolio context line up regardless of which spelling each source uses.
"""

from __future__ import annotations

__all__ = ["canonical_symbol"]

# Quote currencies we strip from a ticker (longer first).
_QUOTE_SUFFIXES = ("-USDT", "-USDC", "-BUSD", "-USD", "-EUR", "-GBP", "-BTC", "-ETH")

# CoinGecko-style names (compacted) -> exchange ticker base.
_CRYPTO_ALIASES = {
    "BITCOIN": "BTC",
    "XBT": "BTC",
    "ETHEREUM": "ETH",
    "BINANCECOIN": "BNB",
    "RIPPLE": "XRP",
    "SOLANA": "SOL",
    "CARDANO": "ADA",
    "DOGECOIN": "DOGE",
    "POLKADOT": "DOT",
    "LITECOIN": "LTC",
    "CHAINLINK": "LINK",
    "TRON": "TRX",
    "AVALANCHE": "AVAX",
    "POLYGON": "MATIC",
    "SHIBAINU": "SHIB",
    "UNISWAP": "UNI",
    "STELLAR": "XLM",
    "COSMOS": "ATOM",
    "MONERO": "XMR",
    "ALGORAND": "ALGO",
    "FILECOIN": "FIL",
    "BITCOINCASH": "BCH",
    "ETHEREUMCLASSIC": "ETC",
    "TETHER": "USDT",
    "USDCOIN": "USDC",
    "AAVE": "AAVE",
    "NEARPROTOCOL": "NEAR",
    "APTOS": "APT",
    "ARBITRUM": "ARB",
    "OPTIMISM": "OP",
}


def canonical_symbol(value: str | None) -> str:
    """Return a source-independent key for matching two symbols.

    ``BTC-USD``, ``BTCUSDT``, ``bitcoin`` and ``BITCOIN`` all reduce to ``BTC``;
    equities such as ``QQQ`` are returned unchanged.
    """

    text = str(value or "").strip().upper()
    if not text:
        return ""
    for suffix in _QUOTE_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    compact = text.replace(" ", "").replace("-", "").replace("_", "").replace(".", "")
    # Also strip a glued quote currency (e.g. BTCUSDT -> BTC).
    for quote in ("USDT", "USDC", "BUSD", "USD"):
        if len(compact) > len(quote) and compact.endswith(quote):
            compact = compact[: -len(quote)]
            break
    return _CRYPTO_ALIASES.get(compact, compact)
