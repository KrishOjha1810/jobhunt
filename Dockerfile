FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud data lives on a mounted volume so SQLite + uploads persist across deploys.
ENV DATA_DIR=/data
EXPOSE 8080

# --proxy-headers + trust the platform's forwarded headers so request.url_for() builds correct
# https:// URLs behind Render/HF/etc. proxies (otherwise the OAuth redirect_uri comes out as http://
# and Google rejects it with redirect_uri_mismatch).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
