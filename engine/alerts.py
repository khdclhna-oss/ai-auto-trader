import os
import requests
import logging

def send_telegram_alert(message: str) -> bool:
    """
    Sends a message exactly as formatted to the user's Telegram chat.
    Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from the environment.
    """
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    
    if not bot_token or not chat_id:
        logging.warning("Skipping Telegram alert: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set.")
        return False
        
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        # Check if the request was successful
        if response.status_code != 200:
            logging.error(f"Telegram API Error {response.status_code}: {response.text}")
            return False
            
        return True
    except Exception as e:
        logging.error(f"Exception while sending Telegram alert: {str(e)}")
        return False
