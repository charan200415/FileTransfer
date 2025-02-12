FROM python:3.9

# Switch to root for system installations
USER root

# Install dependencies
RUN apt-get update && apt-get install -y \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 user

ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PORT=7860

# Clone repository into a temporary subdirectory
RUN --mount=type=secret,id=Access_key,mode=0444,required=true \
    git clone $(cat /run/secrets/Access_key) $HOME/app/gitfolder

# Copy files from gitfolder to app and ensure correct permissions
RUN cp -r $HOME/app/gitfolder/* $HOME/app/ && \
    rm -rf $HOME/app/gitfolder && \
    chown -R user:user $HOME/app

# Set working directory
WORKDIR $HOME/app

# Switch to non-root user
USER user

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade -r requirements.txt
# Copy application files
COPY --chown=user . .
# Expose port 7860
EXPOSE 7860

# Start the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
