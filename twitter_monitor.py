# twitter_monitor.py
import os
import time
import logging
import threading
import asyncio
from twikit import Client

class TwitterMonitor:
    def __init__(self, target_user, auth_token, poll_interval=10):
        self.target_user = target_user
        self.auth_token = auth_token
        self.poll_interval = poll_interval
        self.last_tweet_id = None
        self.running = False
        self.thread = None
        self.callback = None
        self.client = None
        self.loop = None
        self.consecutive_errors = 0
        self.max_errors = 10

    def init_client(self):
        """Initialize twifork Client with auth_token."""
        try:
            self.client = Client('en-US')
            self.client.set_cookies({'auth_token': self.auth_token})
            logging.info(f"[TWITTER] twifork client initialized for @{self.target_user}")
            return True
        except Exception as e:
            logging.error(f"[TWITTER] Failed to init twifork client: {e}")
            return False

    async def async_get_latest_tweet(self):
        """Async method to get the latest tweet."""
        try:
            # 获取用户对象
            user = await self.client.get_user_by_screen_name(self.target_user)
            if user is None:
                logging.warning(f"[TWITTER] Could not find user @{self.target_user}")
                return None

            # 使用 user.id 获取推文
            tweets = await self.client.get_user_tweets(user.id, 'Tweets')
            if tweets and len(tweets) > 0:
                tweet = tweets[0]
                return {
                    'id': tweet.id,
                    'text': tweet.text,
                    'created_at': tweet.created_at,
                    'link': f"https://x.com/{self.target_user}/status/{tweet.id}"
                }
            return None
        except Exception as e:
            logging.error(f"[TWITTER] twifork error: {e}")
            return None

    def get_latest_tweet_sync(self):
        """Synchronous wrapper for async_get_latest_tweet."""
        if self.loop is None:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
        return self.loop.run_until_complete(self.async_get_latest_tweet())

    def start(self, callback):
        if not self.target_user:
            logging.error("[TWITTER] No target user specified.")
            return False

        if not self.auth_token:
            logging.error("[TWITTER] No auth_token provided. Get it from x.com cookies.")
            return False

        if not self.init_client():
            logging.error("[TWITTER] Failed to initialize twifork client.")
            return False

        self.callback = callback
        self.running = True
        self.thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        logging.info(f"[TWITTER] Started — polling @{self.target_user} every {self.poll_interval}s using twifork (async)")
        return True

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        if self.loop:
            self.loop.close()

    def _poll_loop(self):
        consecutive_errors = 0
        while self.running:
            try:
                logging.debug(f"[TWITTER] Polling @{self.target_user}...")
                tweet = self.get_latest_tweet_sync()

                if tweet:
                    tweet_id = tweet.get('id')
                    tweet_text = tweet.get('text', '')

                    if tweet_id and str(tweet_id) != str(self.last_tweet_id):
                        logging.info(f"[TWITTER] NEW TWEET from @{self.target_user}: {tweet_text[:100]}...")
                        if self.callback:
                            self.callback(tweet)
                        self.last_tweet_id = tweet_id
                        consecutive_errors = 0
                    else:
                        logging.debug("[TWITTER] No new tweet.")
                else:
                    consecutive_errors += 1
                    if consecutive_errors == 1:
                        logging.info("[TWITTER] No tweet fetched this cycle.")
                    elif consecutive_errors > 5:
                        logging.warning(f"[TWITTER] {consecutive_errors} consecutive empty polls.")

                if consecutive_errors > self.max_errors:
                    backoff = min(self.poll_interval * 3, 60)
                    logging.warning(f"[TWITTER] Backing off for {backoff}s due to {consecutive_errors} errors.")
                    time.sleep(backoff)

                time.sleep(self.poll_interval)
            except Exception as e:
                logging.error(f"[TWITTER] Poll loop error: {e}")
                consecutive_errors += 1
                backoff = min(self.poll_interval * (2 ** min(consecutive_errors, 5)), 60)
                time.sleep(backoff)
