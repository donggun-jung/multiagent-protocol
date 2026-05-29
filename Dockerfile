# multiagent-protocol bot — container image.
#
# Build:  docker build -t multiagent-protocol .
# Run:    docker run --rm \
#           -e MERGE_GATE_APP_ID -e MERGE_GATE_PRIVATE_KEY \
#           -v "$PWD/config:/app/config:ro" \
#           ghcr.io/donggun-jung/multiagent-protocol:latest tick
#
# Your private config/ is NOT baked into the image (see .dockerignore); mount it
# at runtime so no identity or secret ever lives in the published image.
FROM python:3.12-slim

WORKDIR /app

# Copy only what the package needs to build + run (NOT config/ — see .dockerignore).
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
COPY schemas/ ./schemas/

RUN pip install --no-cache-dir .

# Run as a non-root user (least privilege).
RUN useradd --create-home --uid 10001 botuser && chown -R botuser /app
USER botuser

ENTRYPOINT ["python", "-m", "multiagent_protocol"]
CMD ["tick"]
