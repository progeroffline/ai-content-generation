FROM python:3.12.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY studio ./studio
RUN mkdir /data && chown 65532:65532 /data
USER 65532:65532
EXPOSE 8090
CMD ["python", "-m", "studio.server"]
