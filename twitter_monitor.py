# twitter_monitor.py
import os
import time
import logging
import threading
import requests
import feedparser
from datetime import datetime, timedelta

class TwitterMonitor:
    def __init__(self, target_user, poll_interval=10):
        self.target_user = target_user
        self.poll_interval = poll_interval
        self.last_tweet_id = None
        self.last_tweet_time = None
        self.running = False
        self.thread = None
        self.callback = None
        self.consecutive_errors = 0
        self.max_errors = 10
        
        # List of public Nitter instances to rotate through
        self.nitter_instances = [
            "https://nitter.net",
            "https://nitter.poast.org",
            "https://nitter.lunar.icu",
            "https://nitter.kavin.rocks",
            "https://nitter.1d4.us",
            "https://nitter.space",
            "https://nitter.nl",
            "https://nitter.mint.lgbt",
        ]
        self.current_instance_index = 0

    def get_next_nitter_instance(self):
        """Rotate to next Nitter instance if current one fails."""
        instance = self.nitter_instances[self.current_instance_index]
        self.current_instance_index = (self.current_instance_index + 1) % len(self.nitter_instances)
        return instance

    def fetch_rss(self):
        """Fetch RSS feed from Nitter with fallback rotation."""
        for attempt in range(len(self.nitter_instances)):
            instance = self.nitter_instances[self.current_instance_index]
            url = f"{instance}/{self.target_user}/rss"
            try:
                logging.debug(f"[TWITTER] Fetching RSS from {url}")
                # Use a timeout and proper User-Agent
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
                response = requests.get(url, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    feed = feedparser.parse(response.content)
                    if feed.entries and len(feed.entries) > 0:
                        return feed
                    else:
                        logging.warning(f"[TWITTER] RSS feed from {instance} returned no entries.")
                else:
                    logging.warning(f"[TWITTER] RSS feed from {instance} returned status {response.status_code}")
                
                # Rotate to next instance
                self.current_instance_index = (self.current_instance_index + 1) % len(self.nitter_instances)
                
            except Exception as e:
                logging.warning(f"[TWITTER] Failed to fetch from {instance}: {e}")
                self.current_instance_index = (self.current_instance_index + 1) % len(self.nitter_instances)
                time.sleep(0.5)
        
        return None

    def get_latest_tweet(self):
        """Get latest tweet from RSS feed."""
        try:
            feed = self.fetch_rss()
            if not feed or not feed.entries:
                return None
            
            # Get the first (newest) entry
            entry = feed.entries[0]
            
            # Extract tweet ID from link (e.g., /username/status/123456789)
            link = entry.get('link', '')
            tweet_id = None
            if '/status/' in link:
                tweet_id = link.split('/status/')[-1]
            
            # Extract text from title or summary
            title = entry.get('title', '')
            summary = entry.get('summary', '')
            # Clean HTML tags from summary
            import re
            summary = re.sub(r'<[^>]+>', '', summary)
            text = title if title else summary
            
            if not text:
                return None
            
            # Parse published time
            published = entry.get('published', '')
            pub_time = None
            if published:
                try:
                    pub_time = datetime.strptime(published, '%a, %d %b %Y %H:%M:%S %Z')
                except:
                    try:
                        pub_time = datetime.strptime(published, '%Y-%m-%dT%H:%M:%S%z')
                    except:
                        pass
            
            return {
                'id': tweet_id or str(int(time.time() * 1000)),
                'text': text,
                'created_at': published,
                'link': link
            }
        except Exception as e:
            logging.error(f"[TWITTER] RSS fetch error: {e}")
            return None

    def start(self, callback):
        if not self.target_user:
            logging.error("[TWITTER] No target user specified.")
            return False
        
        self.callback = callback
        self.running = True
        self.thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        logging.info(f"[TWITTER] Started — polling @{self.target_user} via Nitter every {self.poll_interval}s")
        logging.info(f"[TWITTER] Nitter instances: {len(self.nitter_instances)} (will rotate on failure)")
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
                    tweet_text = tweet.get('text', '')
                    
                    # Check if this is a new tweet (by ID or by time)
                    if tweet_id and tweet_id != self.last_tweet_id:
                        logging.info(f"[TWITTER] NEW TWEET from @{self.target_user}: {tweet_text[:100]}...")
                        if self.callback:
                            self.callback(tweet)
                        self.last_tweet_id = tweet_id
                        self.last_tweet_time = time.time()
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
