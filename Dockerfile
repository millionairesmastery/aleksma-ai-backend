# Aleksma AI — FastAPI Backend
# CadQuery + OCP installed via conda (required for top-level imports)
# Actual heavy execution can be offloaded to Modal in production

FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc curl bzip2 libgl1-mesa-glx libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install miniforge (conda) for CadQuery + OCP
RUN curl -fsSL https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh -o /tmp/miniforge.sh \
    && bash /tmp/miniforge.sh -b -p /opt/conda \
    && rm /tmp/miniforge.sh \
    && /opt/conda/bin/conda create -y -n cad python=3.11 -c conda-forge \
    && /opt/conda/bin/conda install -y -n cad -c conda-forge cadquery=2.4.0 \
    && /opt/conda/bin/conda clean -afy

# Use the conda env's Python
ENV PATH="/opt/conda/envs/cad/bin:$PATH"

# Python deps (pip install into the conda env)
COPY requirements.production.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .

# Port (Render expects 10000 by default, but we set PORT env var)
EXPOSE 8000

# Start — use $PORT for Render compatibility
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
