import feedparser

# Stock-specific Google News RSS feeds (English, NSE stocks)
RSS_FEEDS = {
    "RELIANCE":   "https://news.google.com/rss/search?q=Reliance+Industries+NSE&hl=en-IN&gl=IN&ceid=IN:en",
    "TCS":        "https://news.google.com/rss/search?q=TCS+Tata+Consultancy+NSE&hl=en-IN&gl=IN&ceid=IN:en",
    "INFY":       "https://news.google.com/rss/search?q=Infosys+NSE+stock&hl=en-IN&gl=IN&ceid=IN:en",
    "HDFCBANK":   "https://news.google.com/rss/search?q=HDFC+Bank+NSE+stock&hl=en-IN&gl=IN&ceid=IN:en",
    "ICICIBANK":  "https://news.google.com/rss/search?q=ICICI+Bank+NSE+stock&hl=en-IN&gl=IN&ceid=IN:en",
    "WIPRO":      "https://news.google.com/rss/search?q=Wipro+NSE+stock&hl=en-IN&gl=IN&ceid=IN:en",
    "AXISBANK":   "https://news.google.com/rss/search?q=Axis+Bank+NSE+stock&hl=en-IN&gl=IN&ceid=IN:en",
    "KOTAKBANK":  "https://news.google.com/rss/search?q=Kotak+Mahindra+Bank+NSE&hl=en-IN&gl=IN&ceid=IN:en",
    "BAJFINANCE": "https://news.google.com/rss/search?q=Bajaj+Finance+NSE+stock&hl=en-IN&gl=IN&ceid=IN:en",
    "SBIN":       "https://news.google.com/rss/search?q=SBI+State+Bank+India+NSE&hl=en-IN&gl=IN&ceid=IN:en",
    "HINDUNILVR": "https://news.google.com/rss/search?q=Hindustan+Unilever+NSE+stock&hl=en-IN&gl=IN&ceid=IN:en",
    "BHARTIARTL": "https://news.google.com/rss/search?q=Bharti+Airtel+NSE+stock&hl=en-IN&gl=IN&ceid=IN:en",
    "SUNPHARMA":  "https://news.google.com/rss/search?q=Sun+Pharma+NSE+stock&hl=en-IN&gl=IN&ceid=IN:en",
    "ITC":        "https://news.google.com/rss/search?q=ITC+NSE+stock&hl=en-IN&gl=IN&ceid=IN:en",
    "MARUTI":     "https://news.google.com/rss/search?q=Maruti+Suzuki+NSE+stock&hl=en-IN&gl=IN&ceid=IN:en",
}

POSITIVE_WORDS = [
    "beat", "profit", "growth", "surge", "gain", "up", "rise", "strong",
    "record", "buy", "upgrade", "outperform", "bullish", "rally", "positive",
    "expansion", "revenue", "acquisition", "dividend", "buyback", "guidance",
    "outlook", "beat estimates", "exceeds", "order win", "contract",
]
NEGATIVE_WORDS = [
    "miss", "loss", "fall", "drop", "down", "cut", "weak", "sell",
    "downgrade", "concern", "risk", "decline", "lawsuit", "penalty",
    "fraud", "probe", "investigation", "layoff", "slowdown", "default",
    "below estimates", "disappointment", "warning", "cautious", "write-off",
]


def get_news_sentiment(symbol: str) -> int:
    """Returns +1 (positive), 0 (neutral), or -1 (negative) based on recent news."""
    try:
        url  = RSS_FEEDS.get(symbol.upper(), f"https://news.google.com/rss/search?q={symbol}+NSE+stock&hl=en-IN&gl=IN&ceid=IN:en")
        feed = feedparser.parse(url)
        score = 0
        for entry in feed.entries[:15]:
            text = (entry.get("title", "") + " " + entry.get("summary", "")).lower()
            pos = sum(1 for w in POSITIVE_WORDS if w in text)
            neg = sum(1 for w in NEGATIVE_WORDS if w in text)
            score += (1 if pos > neg else -1 if neg > pos else 0)
        return 1 if score > 0 else (-1 if score < 0 else 0)
    except Exception:
        return 0
