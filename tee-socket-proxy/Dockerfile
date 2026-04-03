FROM python:3.11-slim
RUN pip install --no-cache-dir aiohttp
COPY proxy/ /app/proxy/
WORKDIR /app
ENTRYPOINT ["python", "-m", "proxy.main"]
