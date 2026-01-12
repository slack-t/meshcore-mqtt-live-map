FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends nodejs npm git \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN npm install --prefix /app @michaelhart/meshcore-decoder \
  && npm cache clean --force

# Copy all Python modules
COPY *.py /app/
COPY routes /app/routes/
COPY services /app/services/
COPY scripts /app/scripts/
COPY static /app/static/

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
