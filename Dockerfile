FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV DB_PATH=/data/sniper.db
VOLUME /data
CMD ["python", "-m", "sniper.main"]
