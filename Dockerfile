FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=7860

# Run as a non-root user (required by Hugging Face Spaces, good practice everywhere).
RUN useradd -m -u 1000 appuser
WORKDIR /home/appuser/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only the app and the processed dataset go into the image (scrapers & raw data stay out).
COPY --chown=appuser app/ app/
COPY --chown=appuser data/processed/ data/processed/

USER appuser
# Download the embedding model and build the vector index at build time -> fast startup.
RUN python -m app.retriever

EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
