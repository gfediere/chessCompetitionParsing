FROM python:alpine3.20

WORKDIR /usr/src/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r ./requirements.txt

COPY run.py /usr/src/app/run.py
COPY app.py /usr/src/app/app.py
COPY templates /usr/src/app/templates

CMD [ "python", "/usr/src/app/app.py" ]