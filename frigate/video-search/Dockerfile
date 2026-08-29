FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
# CPU-only torch/torchvision - no CUDA needed, this container only runs the text tower.
# Both must come from the same index: a CUDA-index torchvision against a +cpu torch
# mismatches ABI and fails to register its custom ops (torchvision::nms).
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch torchvision \
    && pip install --no-cache-dir -r requirements.txt

COPY video_search_api.py .

EXPOSE 8768
CMD ["python", "video_search_api.py", "--host", "0.0.0.0", "--port", "8768"]
