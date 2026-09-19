COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app_render.py .
COPY scraper.py .
COPY analyze_live.py .
COPY storage.py .
