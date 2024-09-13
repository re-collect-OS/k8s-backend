# NB: For a project-wide python and/or poetry version update, edit:
# - .github/actions/setup/action.yaml
# - infra/http_servers/http_server.Dockerfile
# - infra/http_servers/worker.Dockerfile

# Build image
FROM python:3.11.7 as build

# Env vars for Poetry
ENV \
  POETRY_VERSION="1.7.1" \
  PATH="/root/.local/bin:$PATH"

WORKDIR /app
# Setup env and install deps first (less likely to change)
RUN curl -sSL https://install.python-poetry.org | python3 -
RUN poetry self add 'poethepoet[poetry_plugin]'
COPY ./pyproject.toml ./poetry.* ./
RUN poetry install --no-root --only main --no-interaction
# Copy over and install app code (more likely to change)
COPY ./src ./src
RUN poetry install --only-root --no-interaction

# Final/runtime image
FROM python:3.11.6-slim as runtime

WORKDIR /app
# Copy over build artifacts
COPY --from=build --chown=app:app /app/.venv ./.venv
COPY --from=build --chown=app:app /app/src ./src
# Setup virtual env
ENV \
  PATH="/app/.venv/bin:$PATH" \
  VIRTUAL_ENV="/app/.venv"

# Create and switch to a homeless, non-root user
RUN useradd app
USER app

COPY --chown=app:app ./infra/http_servers/start.sh ./start.sh

CMD ["./start.sh"]
