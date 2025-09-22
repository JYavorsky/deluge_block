# Use the specific Python base image
FROM python:3.11.13

# Set the working directory
WORKDIR /app

# Copy your Python script into the container
COPY ./block.py /app

# Install the deluge-client package
RUN pip install --no-cache-dir deluge-client

# Run the Python script
CMD ["python", "/app/block.py"]
