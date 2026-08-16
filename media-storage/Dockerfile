FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY media-storage/python /app/media-storage/python
RUN pip install --no-cache-dir /app/media-storage/python
RUN mkdir -p /storage/staging /storage/media /storage/temp /state \
    && chown -R 10001:10001 /storage /state
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "upm_media_storage.api:app", "--host", "0.0.0.0", "--port", "8080"]
