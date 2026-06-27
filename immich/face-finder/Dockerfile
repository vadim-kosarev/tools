FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 libjpeg62-turbo \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY face_finder_api.py .
COPY static/ ./static/

EXPOSE 8767
CMD ["python", "face_finder_api.py", "--host", "0.0.0.0", "--port", "8767"]
