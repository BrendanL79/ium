FROM python:3.11-slim

# Install required packages
RUN apt-get update && apt-get install -y \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application
COPY dum.py .

# Create directories for config and state
RUN mkdir -p /config /state

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Default to dry-run mode for safety
ENTRYPOINT ["python", "dum.py"]
CMD ["--dry-run", "--log-level", "INFO", "--state", "/state/docker_update_state.json", "/config/config.json"]