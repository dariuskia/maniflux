# The library service: turns saved links into finished notes (see service.py and README.md)
FROM python:3.13-slim
RUN pip install --no-cache-dir httpx pyyaml beautifulsoup4 watchfiles fastapi uvicorn
WORKDIR /app
COPY library.py fill.py enrich.py service.py ./
CMD ["uvicorn", "service:app", "--host", "0.0.0.0", "--port", "8095", "--proxy-headers"]
