# twitter_monitor.py
import os
import time
import logging
import threading
from Scweet import Scweet

class TwitterMonitor:
    def __init__(self, target_user, auth_token, poll_interval=2):
        self.target_user = target_user
        self.auth_token = auth_token
        self.poll_interval = poll_interval
        self.last_tweet_id = None
        self.scweet = None
        self.running = False
        self.thread = None
        self.callback = None

    def init_scweet(self):
        try:
            self.scweet = Scweet(auth_token=self.auth_token)
            logging.info(f"TwitterMonitor initialized for @{self.target_user}")
            return True
        except Exception as e:
            logging.error(f"Failed to init Scweet: {e}")
            return False

    def get_latest_tweet(self):
        try:
            tweets = self.scweet.search(
                f"from:{self.target_user}",
                since="2026-01-01",  # we only need the latest, but Scweet requires a date
                limit=1,
                save=False
            )
            if tweets and len(tweets) > 0:
                return tweets[0]
            return None
        except Exception as e:
            logging.error(f"Scweet search error: {e}")
            return None

    def start(self, callback):
        if not self.init_scweet():
            return False
        self.callback = callback
        self.running = True
        self.thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        logging.info(f"TwitterMonitor started — polling @{self.target_user} every {self.poll_interval}s")
        return True

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)

    def _poll_loop(self):
        while self.running:
            try:
                tweet = self.get_latest_tweet()
                if tweet:
                    tweet_id = tweet.get('id')
                    if tweet_id and tweet_id != self.last_tweet_id:
                        logging.info(f"NEW TWEET from @{self.target_user}: {tweet.get('text', '')[:100]}...")
                        if self.callback:
                            self.callback(tweet)
                        self.last_tweet_id = tweet_id
                time.sleep(self.poll_interval)
            except Exception as e:
                logging.error(f"Poll loop error: {e}")
                time.sleep(self.poll_interval * 2)  # back off on error
