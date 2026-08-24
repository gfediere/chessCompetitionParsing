FROM python:3.11-slim
RUN useradd -m -u 1000 appuser
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r ./requirements.txt

# Création des deux dossiers dédiés
RUN mkdir -p /app/data
RUN mkdir -p /app/subscriptions

COPY templates /app/templates
COPY static /app/static

COPY run.py /app/run.py
COPY app.py /app/app.py

RUN chown -R appuser:appuser /app
USER appuser
EXPOSE 5000

CMD [ "python", "/app/app.py" ]