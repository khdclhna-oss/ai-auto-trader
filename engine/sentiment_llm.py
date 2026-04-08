import os
from google import genai
import feedparser

def get_llm_sentiment(symbol: str) -> float:
    """
    Upgraded Sentiment Layer using latest google-genai SDK (Gemini 2.0 / 1.5 Flash).
    Returns -1.0 to 1.0.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        # Graceful degradation: If no key, sentiment check is neutral
        return 0.0

    # 1. Fetch latest headlines
    query = symbol.replace(".NS", "")
    feed_url = f"https://news.google.com/rss/search?q={query}+stock+news+india&hl=en-IN&gl=IN&ceid=IN:en"
    
    try:
        feed = feedparser.parse(feed_url)
        headlines = [entry.title for entry in feed.entries[:5]]
        
        if not headlines:
            return 0.0

        # 2. Configure Client (New SDK pattern)
        client = genai.Client(api_key=api_key)
        
        prompt = f"""
        Analyze these Indian market headlines for {symbol}:
        {chr(10).join(headlines)}
        
        Return ONLY a decimal number between -1.0 (extremely bearish) and 1.0 (extremely bullish).
        0 is neutral. Focus on the next 1-5 day price impact.
        """
        
        # 3. Call Gemini 1.5 Flash
        response = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=prompt
        )
        
        score_str = response.text.strip()
        
        # Robust number parsing
        import re
        match = re.search(r"[-+]?\d*\.\d+|\d+", score_str)
        if match:
            score = float(match.group())
            return max(-1.0, min(1.0, score))
            
        return 0.0
        
    except Exception as e:
        print(f"  🧠 LLM (v2) Error for {symbol}: {e}")
        return 0.0
