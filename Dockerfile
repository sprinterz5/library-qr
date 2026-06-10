FROM python:3.12-slim

# Install system dependencies for Playwright and general tools
# We accept the microsoft/playwright image would be easier, but using python base for control
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (chromium only to save space, or all if needed)
# We also need to install system deps for browsers
RUN playwright install --with-deps chromium

# Copy application code
COPY . .

# Expose port
EXPOSE 8000

# Command to run the application
# We don't need the windows-specific loop policy wrapper here
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
