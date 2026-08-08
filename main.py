import asyncio
import os
import tweepy
from binance import AsyncClient
from binance.enums import *
from dotenv import load_dotenv
from sentiment import get_sentiment, Signal

# Load environment variables from .env (local) or Koyeb env vars
load_dotenv()

# ----------------- CONFIG (set via environment variables) -----------------
TWITTER_BEARER = os.getenv("TWITTER_BEARER")
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET = os.getenv("BINANCE_SECRET")

SYMBOL = os.getenv("SYMBOL", "BTCUSDT")        # default BTCUSDT; you can set it to an oil pair if available
TRADE_QUANTITY = float(os.getenv("TRADE_QUANTITY", "0.001"))
# ------------------------------------------------------------------------

class OilBot:
    def __init__(self):
        self.binance = None

    async def init_binance(self):
        # Connect to Binance Testnet (use testnet=True)
        self.binance = await AsyncClient.create(
            BINANCE_API_KEY,
            BINANCE_SECRET,
            testnet=True  # <-- change to False when going live
        )
        print("Binance Testnet client connected.")

    async def on_tweet(self, tweet):
        # Ignore retweets, quotes, replies if you want only original tweets
        if tweet.referenced_tweets:
            return

        text = tweet.text
        if not text or len(text) < 20:   # too short, likely noise
            return

        # Use your sentiment engine (default conflict mode BEAR_BIAS)
        signal = get_sentiment(text, conflict_mode="BEAR_BIAS")

        if signal == Signal.BULL:
            side = SIDE_BUY
        elif signal == Signal.BEAR:
            side = SIDE_SELL
        else:
            # Neutral or stay flat
            return

        print(f"\n[TWEET] {text[:150]}...")
        print(f"[SIGNAL] {signal.value} → Placing order...")
        await self.place_order(side)

    async def place_order(self, side):
        try:
            order = await self.binance.create_order(
                symbol=SYMBOL,
                side=side,
                type=ORDER_TYPE_MARKET,
                quantity=TRADE_QUANTITY
            )
            print(f"[ORDER] {order}")
        except Exception as e:
            print(f"[ORDER ERROR] {e}")

    async def start_stream(self):
        # Create Tweepy async streaming client
        stream = tweepy.AsyncStreamingClient(bearer_token=TWITTER_BEARER)

        # Clear any existing rules and add our own
        rules = await stream.get_rules()
        if rules.data:
            rule_ids = [rule.id for rule in rules.data]
            await stream.delete_rules(rule_ids)

        # ---------- RULES (customise to your needs) ----------
        await stream.add_rules([
            tweepy.StreamRule("from:RaoulGMI OR from:PeterLBrandt OR #OOTT OR #crude OR #WTI OR BTC OR ETH"),
        ])
        # -------------------------------------------------------

        print("Twitter stream started. Waiting for tweets...")
        # The on_tweet method will be called for every incoming tweet
        # We need to override the stream's on_tweet callback. We do this via subclassing.
        # But since we want to keep it simple, we'll use the stream's add_callback.
        # Tweepy's AsyncStreamingClient expects an on_tweet method, so we can set it directly.
        stream.on_tweet = self.on_tweet

        # Start filtering with required fields
        await stream.filter(tweet_fields=["author_id", "created_at", "referenced_tweets"])

async def main():
    bot = OilBot()
    await bot.init_binance()
    await bot.start_stream()

if __name__ == "__main__":
    asyncio.run(main())
