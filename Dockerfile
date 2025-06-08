# Use official lightweight Python image
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Expose port 8080 (Cloud Run default)
EXPOSE 8080

# Run with Gunicorn
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8080"]
