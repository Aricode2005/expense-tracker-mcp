# Use Python 3.13 image
FROM python:3.13-slim

# Install uv for fast dependency resolution
RUN pip install uv

# Set working directory
WORKDIR /app

# Copy dependency file
COPY pyproject.toml .

# Install dependencies globally in the container
RUN uv pip install --system -e .

# Copy the rest of the application
COPY . .

# Expose the port (Render/Railway will inject PORT env var, but this is default)
EXPOSE 8000

# Run the MCP server
CMD ["python", "main.py"]
