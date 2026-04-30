FROM python:3.10-alpine

WORKDIR /app

# Cài đặt thư viện build phụ thuộc cho thư viện werkzeug/sqlalchemy nếu cần
# Alpine cần một chút dependencies để compile
RUN apk add --no-cache gcc musl-dev libffi-dev

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Xoá dependencies build để giảm size ảnh
RUN apk del gcc musl-dev libffi-dev

COPY . .

# Khởi tạo db mặc định
RUN mkdir -p /app/instance && python setup_db.py

EXPOSE 5000

CMD ["python", "run.py"]
