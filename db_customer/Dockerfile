FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data && if [ -f db_customer/state.json ]; then cp db_customer/state.json /data/state.json; else echo "{}" > /data/state.json; fi

EXPOSE 6001

CMD ["python3", "db_customer/server.py", "--host", "0.0.0.0", "--port", "6001", "--state", "/data/state.json"]
