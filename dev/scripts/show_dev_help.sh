#!/bin/bash
set -e

help="🛫 Initial setup

  📦 Install all necessary tools:
      ./dev/scripts/setup_local_dev.sh

  🥾 Bootstrap dependencies (run migrations, create S3 buckets, etc.):
      poetry bootstrap

💻 Developing

  🐳 Manage 3rd party API containers (AWS, Postgres, Weaviate, etc.):
      poetry containers [start|stop|reset]

  🏬 Run the API http server
      poetry run-server api

  🏭 Run workers:
      poetry run-workers <worker-name> [<worker-name> ...]

  ⏫ Upgrade all dependencies:
      poetry dep-upgrade

🧪 Testing

  🏎️  Unit tests (fast):
      poetry unit-test

  🐌 Integration tests (slower):
      poetry test

🏗️  Deploying

  🔧 Setup pulumi:
      pulumi login
      pulumi stack select

  🔼 Bump version
      poetry version [patch|minor|major|<version>]

  🚀 Go live!
      poetry deploy [dev|prod]"

echo "$help"
