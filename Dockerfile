# Use Python 3.10 slim image as base
FROM python:3.10-slim

# Create non-root user
RUN useradd -m -u 1000 user

# Set environment variables
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PORT=7860

# Create and set working directory
WORKDIR $HOME/app

# Copy requirements first to leverage Docker cache
COPY --chown=user requirements.txt .

# Switch to non-root user
USER user

# Install dependencies
RUN pip install --user -r requirements.txt

# Copy application files
COPY --chown=user . .

# Create uploads directory
RUN mkdir -p uploads

# Expose port
EXPOSE ${PORT}

# Command to run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860", "--reload"]