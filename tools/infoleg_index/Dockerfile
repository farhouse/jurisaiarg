FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/models HF_HUB_DISABLE_TELEMETRY=1 TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=2 MKL_NUM_THREADS=2
WORKDIR /app
COPY tools/infoleg_index/requirements*.txt ./
RUN pip install --no-cache-dir torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu && pip install --no-cache-dir -r requirements.txt -r requirements-ml.txt
COPY tools/infoleg_index/*.py tools/infoleg_index/*.sql ./
COPY web/review/embedding.py web/review/text_integrity.py web/review/pilot_contract.py ./
HEALTHCHECK --interval=15s --timeout=6s --start-period=30s --retries=5 CMD ["python", "worker.py", "--healthcheck"]
CMD ["python", "worker.py"]
