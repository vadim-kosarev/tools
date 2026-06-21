FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY face_search_web.py .

EXPOSE 8765
CMD ["python", "face_search_web.py", "--host", "0.0.0.0", "--port", "8765"]
