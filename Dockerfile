FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ./
COPY templates ./templates
COPY static ./static
RUN mkdir -p /app/data
ENV UTM_DB_PATH=/app/data/utm.db PORT=8000
EXPOSE 8000
CMD ["gunicorn","-w","2","-b","0.0.0.0:8000","--access-logfile","-","app:app"]
