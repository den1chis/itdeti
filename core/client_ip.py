"""Client IP address for rate limiting, safe behind a reverse proxy.

Why not `uvicorn --proxy-headers --forwarded-allow-ips='*'`: then uvicorn takes the LEFT-most address of
X-Forwarded-For, and that part of the header is written by the client, so anyone could pick a fresh
"address" for every request and never hit the limit.

- BEHIND_PROXY=false (default, local work): the socket address; every header is ignored.
- BEHIND_PROXY=true (Render):
  1. CF-Connecting-IP. Render puts Cloudflare in front of every service; Cloudflare sets this header to the
     address of the client and OVERWRITES whatever the client sent, so it cannot be forged from outside.
     (Night 4: on Render the right-most X-Forwarded-For address turned out to be a changing internal 10.x
     address, so the X-Forwarded-For rule alone gave every request its own bucket.)
  2. No such header, several values or not an IP address -> the N-th address from the right of
     X-Forwarded-For (N = TRUSTED_PROXY_HOPS, >= 1): a proxy APPENDS the address it saw, so the N-th one
     from the right was added by our own outermost trusted proxy; everything to the left may be forged.
  3. Header missing, too short or not an IP address -> the socket address."""
import ipaddress
from typing import Optional

from starlette.requests import Request

from core.config import settings


def _valid_ip(value: str) -> Optional[str]:
    value = value.strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def client_ip(request: Request, behind_proxy: Optional[bool] = None, trusted_hops: Optional[int] = None) -> str:
    socket_ip = request.client.host if request.client else "unknown"
    behind_proxy = settings.BEHIND_PROXY if behind_proxy is None else behind_proxy
    if not behind_proxy:
        return socket_ip
    # Exactly one CF-Connecting-IP with one address; anything else is not what Cloudflare sends.
    cf_values = request.headers.getlist("cf-connecting-ip")
    if len(cf_values) == 1:
        cf_ip = _valid_ip(cf_values[0])
        if cf_ip:
            return cf_ip
    hops = max(1, settings.TRUSTED_PROXY_HOPS if trusted_hops is None else trusted_hops)
    # Several X-Forwarded-For headers are equivalent to one comma separated list (RFC 7230, 3.2.2).
    parts = [p for h in request.headers.getlist("x-forwarded-for") for p in h.split(",") if p.strip()]
    if len(parts) < hops:
        return socket_ip
    return _valid_ip(parts[-hops]) or socket_ip
