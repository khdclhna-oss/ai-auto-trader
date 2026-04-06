import feedparser
import re

RSS_FEEDS = {
    "RELIANCE": "https://www.moneycontrol.com/rss/MCtopnews.xml",
    "TCS": "https://www.moneycontrol.com/rss/MCtopnews.xml",
    "INFY": "https://www.moneycontrol.com/rss/MCtopnews.xml",
    "HDFCBANK": "https://www.moneycontrol.com/rss/MCtopnews.xml",
    "ICICIBANK": "https://www.moneycontrol.com/rss/MCtopnews.xml",
}

POSITIVE_WORDS = ["beat", "profit", "growth", "surge", "gain", "up", "rise", "strong", "record", "buy", "upgrade"]
NEGATIVE_WORDS = ["miss", "loss", "fall", "drop", "down", "cut", "weak", "sell", "downgrade", "concern", "risk"]

def get_news_sentiment(symbol: str) -> int:
    feed = feedparser.parse(RSS_FEEDS.get(symbol, RSS_FEEDS["RELIANCE"]))
    score = 0
    for entry in feed.entries[:20]:
        text = (entry.get("title", "") + " " + entry.get("summary", "")).lower()
        if symbol.lower() in text or symbol.lower().replace("bank", "") in text:
            pos = sum(1 for w in POSITIVE_WORDS if w in text)
            neg = sum(1 for w in NEGATIVE_WORDS if w in text)
            score += 1 if pos > neg else (-1 if neg > pos else 0)
    return 1 if score > 0 else (-1 if score < 0 else 0)
