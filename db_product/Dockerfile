FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data && if [ -f db_product/state.db ]; then cp db_product/state.db /data/state.db; else touch /data/state.db; fi

EXPOSE 6002

CMD ["python3", "db_product/product_server.py", "--host", "0.0.0.0", "--port", "6002", "--state", "/data/state.db"]
