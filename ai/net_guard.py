# ai/net_guard.py
import time, random, threading
from collections import deque
from typing import Optional, Dict, Tuple

class TokenBucket:
    def __init__(self, capacity: int = 35, window_sec: int = 60):
        self.capacity = capacity
        self.window_sec = window_sec
        self.tokens = capacity
        self.q = deque()
        self.lock = threading.Lock()

    def acquire(self) -> bool:
        with self.lock:
            now = time.time()
            while self.q and now - self.q[0] > self.window_sec:
                self.q.popleft()
                self.tokens = min(self.capacity, self.tokens + 1)
            if self.tokens > 0:
                self.tokens -= 1
                self.q.append(now)
                return True
            return False

    def wait(self):
        while not self.acquire():
            time.sleep(0.25)

class PriceCache:
    def __init__(self, ttl_sec: int = 60):
        self.ttl = ttl_sec
        self.store: Dict[str, Tuple[float, float]] = {}
        self.lock = threading.Lock()

    def get(self, symbol: str) -> Optional[float]:
        with self.lock:
            v = self.store.get(symbol)
            if not v: return None
            price, ts = v
            if time.time() - ts <= self.ttl:
                return price
            return None

    def set(self, symbol: str, price: float):
        with self.lock:
            self.store[symbol] = (price, time.time())

BUCKET = TokenBucket(capacity=35, window_sec=60)
CACHE  = PriceCache(ttl_sec=60)

def backoff_sleep(attempt: int):
    wait = min(2 ** attempt, 16) + random.uniform(0, 0.25)
    time.sleep(wait)
