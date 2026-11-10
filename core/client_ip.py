"""Client IP address for rate limiting, safe behind a reverse proxy.

Why not `uvicorn --proxy-headers --forwarded-allow-ips='*'`: then uvicorn takes the LEFT-most address of
X-Forwarded-For, and that part of the header is written by the client, so anyone could pick a fresh
"address" for every request and never hit the limit.

A proxy APPENDS the address it saw to the right end of X-Forwarded-For. With N trusted proxies in front of
the app (Render: TRUSTED_PROXY_HOPS=1), the N-th address from the right was added by our own outermost
trusted proxy and is the real client address; everything to the left of it may be forged.

- BEHIND_PROXY=false (default, local work): the socket address; X-Forwarded-For is ignored.
- BEHIND_PROXY=true: the N-th address from the right of X-Forwarded-For (N = TRUSTED_PROXY_HOPS, >= 1);
  if the header is missing, too short or not an IP address -> the socket address."""
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
    hops = max(1, settings.TRUSTED_PROXY_HOPS if trusted_hops is None else trusted_hops)
    # Several X-Forwarded-For headers are equivalent to one comma separated list (RFC 7230, 3.2.2).
    parts = [p for h in request.headers.getlist("x-forwarded-for") for p in h.split(",") if p.strip()]
    if len(parts) < hops:
        return socket_ip
    return _valid_ip(parts[-hops]) or socket_ip
