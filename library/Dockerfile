# The library service: fills in new books, films, shows and music (see enrich.py)
FROM python:3.13-slim
RUN pip install --no-cache-dir httpx pyyaml
WORKDIR /app
COPY library.py enrich.py ./
CMD ["python", "-u", "enrich.py", "--watch"]
