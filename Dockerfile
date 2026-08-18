FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY iddaa/ iddaa/
COPY tahmin.py .

# Maç arşivi buraya iner; compose'ta kalıcı volume bağlanır
RUN mkdir -p /app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/ping', timeout=8)"

# Tek işçi + çoklu thread: bellekte tek veri kopyası tutulur, uzun istekler
# (veri indirme ~1-3 dk, backtest ~40 sn) diğer sekmeleri kilitlemez.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "8", "--timeout", "300", "iddaa.web:uygulama_olustur()"]
