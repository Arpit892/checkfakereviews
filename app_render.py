"""
app_render.py
The web server for the HOSTED demo (checkfakereviews.online), deployed on
Render's free tier. Uses precomputed results (analyze_demo.py) instead of
live scraping + GPT-2, since that stack needs more RAM than the free tier
provides, and Amazon blocks cloud-server IPs more aggressively than home
connections anyway.

For local development with the FULL live pipeline, use app.py instead.

Usage on Render:
    Build command: pip install -r requirements-render.txt
    Start command: uvicorn app_render:app --host 0.0.0.0 --port $PORT
"""

import os
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from analyze_demo import analyze_product_demo
from demo_data import list_demo_products

app = FastAPI(title="Review Trust Checker (Demo)")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


class CheckRequest(BaseModel):
    url: str


@app.get("/")
def serve_frontend():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/demo-products")
def demo_products():
    """Lets the frontend show which product links are available to try."""
    return {"products": list_demo_products()}


@app.post("/api/check")
def check_product(payload: CheckRequest):
    if not payload.url or not payload.url.strip():
        return {"error": "Please provide a product URL."}
    return analyze_product_demo(payload.url.strip())
