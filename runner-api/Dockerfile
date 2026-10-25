FROM python:3.12-slim
ARG BUILD_VERSION=dev
LABEL cloudrunner.version=$BUILD_VERSION
WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.124.0 uvicorn==0.38.0 docker==7.1.0
COPY main.py .
COPY sandbox.py /sandbox.py
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--limit-concurrency", "64"]
