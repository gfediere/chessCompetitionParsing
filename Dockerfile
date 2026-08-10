FROM python:alpine3.20

WORKDIR /usr/src/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r ./requirements.txt

# Création des deux dossiers dédiés
RUN mkdir -p /usr/src/app/data
RUN mkdir -p /usr/src/app/subscriptions

COPY templates /usr/src/app/templates
COPY run.py /usr/src/app/run.py
COPY app.py /usr/src/app/app.py

EXPOSE 5000

CMD [ "python", "/usr/src/app/app.py" ]