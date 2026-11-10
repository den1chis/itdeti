FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Port from $PORT (Render sets it), 8000 when unset. No --reload; no --proxy-headers / --forwarded-allow-ips
# (the client IP for rate limiting comes from core/client_ip.py, BEHIND_PROXY / TRUSTED_PROXY_HOPS).
# "exec" makes uvicorn PID 1, so it receives SIGTERM and shuts down cleanly on redeploy.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
