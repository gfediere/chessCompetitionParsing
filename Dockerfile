FROM python:alpine3.20

WORKDIR /usr/src/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r ./requirements.txt

COPY run.py /usr/src/app/run.py

CMD [ "python", "/usr/src/app/run.py" ]