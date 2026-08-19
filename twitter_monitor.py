# twitter_monitor.py
import os
import time
import logging
import threading
from Scweet import Scweet
from Scweet.scweet_db import ScweetDB

class TwitterMonitor:
    def __init__(self, target_user, auth_token, poll_interval=10):
        self.target_user = target_user
        self.auth_token = auth_token
        self.poll_interval = poll_interval
        self.last_tweet_id = None
        self.scweet = None
        self.running = False
        self.thread = None
        self.callback = None
        self.reset_count = 0
        self.max_resets = 3

    def init_scweet(self):
        try:
            self.scweet = Scweet(auth_token=self.auth_token)
            logging.info(f"TwitterMonitor initialized for @{self.target_user}")
            # Log account status
            db = ScweetDB()
            accounts = db.inspect_accounts()
            logging.info(f"Account status: {accounts}")
            return True
        except Exception as e:
            logging.error(f"Failed to init Scweet: {e}")
            return False

    def get_latest_tweet(self):
        try:
            tweets = self.scweet.search(
                f"from:{self.target_user}",
                since="2026-01-01",
                limit=1,
                save=False
            )
            if tweets and len(tweets) > 0:
                return tweets[0]
            return None
        except Exception as e:
            error_msg = str(e)
            logging.error(f"Scweet search error: {error_msg}")
            if "No eligible accounts" in error_msg:
                logging.error("ACCOUNT RATE LIMITED – triggering auto-reset.")
                self._auto_reset()
            return None

    def _auto_reset(self):
        """Nuclear option: delete the state DB and reinitialize."""
        if self.reset_count >= self.max_resets:
            logging.critical(f"Max resets ({self.max_resets}) reached. Twitter monitor will stop.")
            self.running = False
            return
        self.reset_count += 1
        logging.warning(f"Auto-reset #{self.reset_count} triggered. Deleting scweet_state.db...")
        try:
            os.remove("scweet_state.db")
            logging.info("scweet_state.db deleted.")
        except FileNotFoundError:
            logging.info("scweet_state.db not found, skipping delete.")
        except Exception as e:
            logging.error(f"Failed to delete scweet_state.db: {e}")
        # Reinitialize
        if self.init_scweet():
            logging.info("Scweet reinitialized after reset.")
        else:
            logging.error("Failed to reinitialize after reset.")

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
        consecutive_errors = 0
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
                        consecutive_errors = 0
                time.sleep(self.poll_interval)
            except Exception as e:
                logging.error(f"Poll loop error: {e}")
                consecutive_errors += 1
                backoff = min(self.poll_interval * (2 ** consecutive_errors), 60)
                logging.info(f"Backing off for {backoff}s")
                time.sleep(backoff)
