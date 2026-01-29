FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 6003

CMD ["python3", "server_buyer/server.py", "--host", "0.0.0.0", "--port", "6003", "--customer-host", "db_customer", "--customer-port", "6001", "--product-host", "db_product", "--product-port", "6002"]
