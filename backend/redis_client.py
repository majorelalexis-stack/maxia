"""MAXIA Redis Client V12 — Cache, rate limiting, sessions with graceful fallback."""
import json, time, os
from typing import Any, Optional


class RedisClient:
    """Redis async client with in-memory fallback when Redis is unavailable."""

    def __init__(self):
        self._redis = None
        self._connected = False
        # In-memory fallback stores
        self._mem_cache: dict[str, tuple[Any, float]] = {}  # key -> (value, expires_at)
        self._mem_rate: dict[str, list[float]] = {}  # identifier -> [timestamps]

    # ── Lifecycle ──

    async def connect(self, redis_url: str = ""):
        url = redis_url or os.getenv("REDIS_URL", "")
        if not url:
            print("[Redis] No REDIS_URL — using in-memory fallback")
            return
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(
                url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
            )
            await self._redis.ping()
            self._connected = True
            print(f"[Redis] Connected to {url.split('@')[-1] if '@' in url else url}")
        except Exception as e:
            print(f"[Redis] Connection failed ({e}) — using in-memory fallback")
            self._redis = None
            self._connected = False

    async def close(self):
        if self._redis:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None
            self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected and self._redis is not None

    # ── Cache: get / set ──

    async def cache_get(self, key: str) -> Optional[Any]:
        if self.is_connected:
            try:
                val = await self._redis.get(key)
                if val is not None:
                    try:
                        return json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        return val
                return None
            except Exception:
                pass
        # Fallback
        entry = self._mem_cache.get(key)
        if entry:
            value, expires_at = entry
            if expires_at == 0 or time.time() < expires_at:
                return value
            del self._mem_cache[key]
        return None

    async def cache_set(self, key: str, value: Any, ttl: int = 300):
        serialized = json.dumps(value) if not isinstance(value, str) else value
        if self.is_connected:
            try:
                await self._redis.set(key, serialized, ex=ttl)
                return
            except Exception:
                pass
        # Fallback
        self._mem_cache[key] = (value, time.time() + ttl if ttl > 0 else 0)
        # Periodic cleanup of expired entries
        if len(self._mem_cache) > 5000:
            self._cleanup_mem_cache()

    async def cache_delete(self, key: str):
        if self.is_connected:
            try:
                await self._redis.delete(key)
                return
            except Exception:
                pass
        self._mem_cache.pop(key, None)

    # ── Cache: prices (convenience) ──

    async def cache_prices(self, data: Any, ttl: int = 30):
        """Cache price data with a short TTL (default 30s)."""
        await self.cache_set("maxia:prices", data, ttl=ttl)

    async def get_cached_prices(self) -> Optional[Any]:
        return await self.cache_get("maxia:prices")

    # ── Rate limiting (sliding window via sorted sets) ──

    async def rate_limit_check(self, identifier: str, limit: int = 60, window: int = 60) -> bool:
        """
        Check if identifier is within rate limit.
        Returns True if request is ALLOWED, False if rate-limited.
        Uses Redis sorted sets for a sliding window approach.
        """
        now = time.time()
        key = f"maxia:rate:{identifier}"

        if self.is_connected:
            try:
                pipe = self._redis.pipeline()
                # Remove expired entries
                pipe.zremrangebyscore(key, 0, now - window)
                # Count current entries
                pipe.zcard(key)
                # Add new entry
                pipe.zadd(key, {str(now): now})
                # Set expiry on the key
                pipe.expire(key, window + 10)
                results = await pipe.execute()
                current_count = results[1]
                if current_count >= limit:
                    # Remove the entry we just added
                    await self._redis.zrem(key, str(now))
                    return False
                return True
            except Exception:
                pass

        # Fallback to in-memory
        timestamps = self._mem_rate.get(identifier, [])
        timestamps = [t for t in timestamps if t > now - window]
        if len(timestamps) >= limit:
            self._mem_rate[identifier] = timestamps
            return False
        timestamps.append(now)
        self._mem_rate[identifier] = timestamps
        # Cleanup
        if len(self._mem_rate) > 10000:
            self._cleanup_mem_rate()
        return True

    # ── Helpers ──

    def _cleanup_mem_cache(self):
        now = time.time()
        expired = [k for k, (_, exp) in self._mem_cache.items() if exp > 0 and exp < now]
        for k in expired:
            del self._mem_cache[k]

    def _cleanup_mem_rate(self):
        now = time.time()
        expired = [k for k, ts in self._mem_rate.items() if not ts or ts[-1] < now - 120]
        for k in expired:
            del self._mem_rate[k]

    def get_stats(self) -> dict:
        return {
            "connected": self.is_connected,
            "backend": "redis" if self.is_connected else "in-memory",
            "mem_cache_keys": len(self._mem_cache),
            "mem_rate_keys": len(self._mem_rate),
        }


# Singleton instance
redis_client = RedisClient()
