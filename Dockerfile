#FROM python:3.11.5-slim-bullseye
FROM python:alpine3.17

WORKDIR /usr/src/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r ./requirements.txt

COPY run.py /usr/src/app/run.py

CMD [ "python", "/usr/src/app/run.py" ]

