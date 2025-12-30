FROM python:3.11-slim

WORKDIR /app

# Install uv using pip
RUN pip install --no-cache-dir uv

# Copy dependency files
COPY pyproject.toml .

# Install dependencies using uv
RUN uv pip install --system --no-cache -r pyproject.toml

# Copy application files
COPY app.py .
COPY templates/ templates/
COPY static/ static/

# Expose port
EXPOSE 8000

# Run application
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
