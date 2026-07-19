# Dockerfile for the hosted demo (checkfakereviews.online)
# Builds the LIGHTWEIGHT version only (app_render.py) - no Playwright, no
# PyTorch/transformers - since those need more RAM than Render's free tier
# provides. The full local pipeline (app.py) is unaffected and still used
# for local development / the professor demo.

FROM python:3.11-slim

WORKDIR /app

COPY requirements-render.txt .
RUN pip install --no-cache-dir -r requirements-render.txt

COPY app_render.py analyze_demo.py demo_data.py ./
COPY static/ ./static/

# Render sets the PORT environment variable at runtime (defaults to 10000).
# Shell form (not exec form) is required here so $PORT actually expands.
CMD uvicorn app_render:app --host 0.0.0.0 --port $PORT
