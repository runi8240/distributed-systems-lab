FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data && if [ -f db_customer/state.db ]; then cp db_customer/state.db /data/state.db; else touch /data/state.db; fi

EXPOSE 6001

CMD ["python3", "db_customer/customer_server.py", "--host", "0.0.0.0", "--port", "6001", "--state", "/data/state.db"]
