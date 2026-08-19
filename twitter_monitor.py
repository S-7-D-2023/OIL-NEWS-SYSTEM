# twitter_monitor.py
import os
import time
import logging
import threading
import requests
import json
from Scweet import Scweet

class TwitterMonitor:
    def __init__(self, target_user, auth_token, poll_interval=30):
        self.target_user = target_user
        self.auth_token = auth_token
        self.poll_interval = poll_interval
        self.last_tweet_id = None
        self.running = False
        self.thread = None
        self.callback = None
        self.scweet = None
        self.consecutive_errors = 0
        self.max_errors = 10

    def init_scweet(self):
        """Initialize Scweet with auth_token."""
        try:
            self.scweet = Scweet(auth_token=self.auth_token)
            logging.info(f"[TWITTER] Scweet initialized for @{self.target_user}")
            return True
        except Exception as e:
            logging.error(f"[TWITTER] Failed to init Scweet: {e}")
            return False

    def get_latest_tweet_scweet(self):
        """Get latest tweet using Scweet's GraphQL API."""
        try:
            # Scweet v4+ uses GraphQL SearchTimeline
            tweets = self.scweet.search(
                f"from:{self.target_user}",
                since="2026-01-01",
                limit=1,
                save=False
            )
            if tweets and len(tweets) > 0:
                tweet = tweets[0]
                logging.debug(f"[TWITTER] Scweet found tweet: {tweet.get('text', '')[:50]}...")
                return {
                    'id': tweet.get('id'),
                    'text': tweet.get('text', ''),
                    'created_at': tweet.get('created_at')
                }
            return None
        except Exception as e:
            error_msg = str(e)
            logging.error(f"[TWITTER] Scweet search error: {error_msg}")
            
            # Check for specific Scweet errors
            if "No eligible accounts" in error_msg:
                logging.warning("[TWITTER] Account rate-limited. Waiting longer...")
                # Don't auto-reset – Scweet handles this internally
                return None
            return None

    def get_latest_tweet_direct(self):
        """Fallback: direct API call (likely to fail but kept for completeness)."""
        try:
            url = f"https://api.x.com/1.1/statuses/user_timeline.json?screen_name={self.target_user}&count=1"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
            }
            cookies = {"auth_token": self.auth_token}
            response = requests.get(url, headers=headers, cookies=cookies, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data and isinstance(data, list) and len(data) > 0:
                    tweet = data[0]
                    return {
                        'id': tweet.get('id_str'),
                        'text': tweet.get('text', ''),
                        'created_at': tweet.get('created_at')
                    }
            else:
                logging.debug(f"[TWITTER] Direct API fallback returned {response.status_code}")
            return None
        except Exception as e:
            logging.debug(f"[TWITTER] Direct API fallback error: {e}")
            return None

    def get_latest_tweet(self):
        """Primary: Scweet GraphQL. Fallback: direct API."""
        # Primary: Scweet
        tweet = self.get_latest_tweet_scweet()
        if tweet:
            return tweet
        
        # Fallback: direct API (may fail, but we try)
        logging.debug("[TWITTER] Scweet returned no tweet, trying direct fallback...")
        return self.get_latest_tweet_direct()

    def start(self, callback):
        if not self.auth_token:
            logging.error("[TWITTER] No auth_token provided. Twitter monitor disabled.")
            return False
        
        if not self.init_scweet():
            logging.error("[TWITTER] Failed to initialize Scweet. Twitter monitor disabled.")
            return False
        
        self.callback = callback
        self.running = True
        self.thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        logging.info(f"[TWITTER] Started — polling @{self.target_user} every {self.poll_interval}s")
        return True

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)

    def _poll_loop(self):
        consecutive_errors = 0
        while self.running:
            try:
                logging.debug(f"[TWITTER] Polling @{self.target_user}...")
                tweet = self.get_latest_tweet()
                if tweet:
                    tweet_id = tweet.get('id')
                    if tweet_id and tweet_id != self.last_tweet_id:
                        tweet_text = tweet.get('text', '')
                        logging.info(f"[TWITTER] NEW TWEET from @{self.target_user}: {tweet_text[:100]}...")
                        if self.callback:
                            self.callback(tweet)
                        self.last_tweet_id = tweet_id
                        consecutive_errors = 0
                    else:
                        logging.debug("[TWITTER] No new tweet.")
                else:
                    # Only log if we have consecutive errors
                    consecutive_errors += 1
                    if consecutive_errors == 1:
                        logging.info("[TWITTER] No tweet fetched this cycle (waiting for new tweets).")
                    elif consecutive_errors > 5:
                        logging.warning(f"[TWITTER] {consecutive_errors} consecutive empty polls.")
                
                # Backoff on repeated errors
                if consecutive_errors > self.max_errors:
                    backoff = min(self.poll_interval * 3, 120)
                    logging.warning(f"[TWITTER] Backing off for {backoff}s due to {consecutive_errors} errors.")
                    time.sleep(backoff)
                
                time.sleep(self.poll_interval)
            except Exception as e:
                logging.error(f"[TWITTER] Poll loop error: {e}")
                consecutive_errors += 1
                backoff = min(self.poll_interval * (2 ** min(consecutive_errors, 5)), 120)
                time.sleep(backoff)
