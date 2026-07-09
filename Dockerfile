FROM tuki-deps:latest
COPY . .
CMD ["uvicorn", "backend.main:api", "--host", "0.0.0.0", "--port", "8000"]
