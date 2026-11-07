FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py *.sql ./
HEALTHCHECK --interval=15s --timeout=8s --start-period=30s --retries=3 \
    CMD ["python", "worker.py", "--healthcheck"]
CMD ["python", "worker.py"]
