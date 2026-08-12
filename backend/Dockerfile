FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# data/ is excluded from the image by .dockerignore — it holds multi-GB source
# dumps and used to be baked in, making this image 11.8GB instead of 533MB. The
# DIRECTORY still has to exist: importer scripts write caches under /app/data,
# and in dev it arrives via the bind mount while the public worker mounts a
# named volume there. Creating it here means the image works even when neither
# is present, instead of failing on a missing path the first time an import runs.
RUN mkdir -p /app/data

COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
