FROM python:3.12-slim
WORKDIR /app

# Unbuffered stdout, and it is not a nicety here.
#
# Python block-buffers stdout when it is a pipe, which is exactly what Docker
# gives it — so up to 8KB of output sits in the process rather than reaching
# `docker logs`. uvicorn installs its own logging and is unaffected, which is why
# the API looked fine and hid the problem. `python worker.py` is not: the worker
# ran for SIX DAYS after a restart without emitting a single line, including its
# own startup banner. It was alive and working the whole time — 47 database
# connections, tasks running — but from outside it was indistinguishable from
# hung, and every log-based check (the watchdog, `docker logs`, any attempt to
# confirm a loop had fired) silently read nothing and concluded nothing.
#
# One line, and every process built from this image reports what it is doing.
ENV PYTHONUNBUFFERED=1

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
