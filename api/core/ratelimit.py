"""Per-IP token bucket for Live requests. In memory, so it is per instance and resets on a cold start."""
import os
import threading
import time

_buckets = {}
_lock = threading.Lock()


def client_ip(request):
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def allow(ip):
    """Spend one token for ip. DOCK_LIVE_PER_MIN sets the rate (default 10); 0 turns the limit off."""
    per_min = float(os.environ.get("DOCK_LIVE_PER_MIN", "10"))
    if per_min <= 0:
        return True
    now = time.monotonic()
    with _lock:
        if len(_buckets) > 10000:
            _buckets.clear()
        tokens, last = _buckets.get(ip, (per_min, now))
        tokens = min(per_min, tokens + (now - last) * per_min / 60)
        allowed = tokens >= 1
        _buckets[ip] = (tokens - 1 if allowed else tokens, now)
    return allowed
