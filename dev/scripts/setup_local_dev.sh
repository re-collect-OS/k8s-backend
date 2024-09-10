#!/bin/bash
set -e

# Script that performs installation and initial setup of all required software
# tools for development on this project. Safe to re-run.

while getopts "vy" opt; do
  case $opt in
    v)
      verbose=true  # Set verbose to true if -v is provided
      ;;
    y)
      yes=true  # Set yes to true if -y is provided
      ;;
    \?)
      echo "Invalid option: -$OPTARG" >&2
      exit 1
      ;;
  esac
done

if [[ "$verbose" = true ]]; then
  out="/dev/stdout"
else
  out="/dev/null"
fi

case "$OSTYPE" in
  "darwin"*)
    pkgman="homebrew" ;;
  "linux-gnu"*)
    # NB: This assumes Ubuntu; it'll have to be updated if another developer
    # prefers other linux distros.
    pkgman="apt" ;;
  *)
    echo "⚠️ Unsupported OS: $OSTYPE"
    exit 1
    ;;
esac

# Check if running in a containerized environment.
if [[ -f /.dockerenv ]]; then
  in_container="devcontainer"
elif [[ -n $WSL_DISTRO_NAME ]]; then
  in_container="wsl"
fi

update_packages() {
  echo "📦 Refreshing $pkgman packages..."
  case "$pkgman" in
    "homebrew")
      brew update > $out ;;
    "apt")
      sudo apt-get update > $out ;;
    *)
      echo "⚠️ Unsupported package manager: $pkgman"
      exit 1
      ;;
  esac
}

install_git() {
  echo -e "\n🐙 Installing git..."
  case "$pkgman" in
    "homebrew")
      brew install git git-lfs > $out ;;
    "apt")
      sudo apt-get install -y git git-lfs > $out ;;
    *)
      echo "⚠️ Unsupported package manager: $pkgman"
      echo "  See: https://git-scm.com/download/"
      ;;
  esac
}

install_curl() {
  echo -e "\n🌐 Installing curl..."
  case "$pkgman" in
    "homebrew")
      brew install curl > $out ;;
    "apt")
      sudo apt-get install -y curl > $out ;;
    *)
      echo "⚠️ Unsupported package manager: $pkgman"
      echo "  See: https://curl.se/download.html"
      ;;
  esac
}

install_jq() {
  echo -e "\n🔧 Installing jq..."
  case "$pkgman" in
    "homebrew")
      brew install jq > $out ;;
    "apt")
      sudo apt-get install -y jq > $out ;;
    *)
      echo "⚠️ Unsupported package manager: $pkgman"
      echo "  See: https://stedolan.github.io/jq/download/"
      ;;
  esac
}

install_python() {
  echo -e "\n🐍 Installing python3.11..."
  case "$pkgman" in
    "homebrew")
      brew install python@3.11 > $out ;;
    "apt")
      sudo apt-get install -y python3.11-dev python3.11-venv > $out ;;
    *)
      echo "⚠️ Unsupported package manager: $pkgman"
      echo "  See: https://www.python.org/downloads/"
      ;;
  esac
}

install_docker() {
  echo -e "\n🐳 Installing docker..."
  case "$pkgman" in
    "homebrew")
      # Homebrew casks don't fail gracefully if the app is already installed;
      # check for that case and skip (with information) if so.
      if ! brew ls --versions --cask discord > /dev/null && [[ -d "/Applications/Docker.app" ]]; then
        echo "⚠️  Docker.app already installed but not managed by homebrew; skipping..."
        echo "   To install with homebrew, refer to https://docs.docker.com/desktop/uninstall/ and re-run this script."
        return
      fi

      brew install --cask docker > $out
      ;;
    "apt")
      case "$in_container" in
        "devcontainer")
          # Ubuntu in devcontainer; install CLI to interact with host docker.
          sudo apt-get install -y moby-cli moby-compose > $out
          ;;
        "wsl")
          # Ubuntu in WSL; install CLI to interact with host docker.
          sudo apt-get install -y docker-ce-cli > $out
          ;;
        *)
          # Ubuntu native; install docker engine
          sudo apt-get install -y docker-ce > $out
      esac
      ;;
    *)
      echo "⚠️ Unsupported package manager: $pkgman"
      echo "  See: https://docs.docker.com/engine/install/"
      ;;
  esac
}

install_psql() {
  echo -e "\n🐘 Installing psql..."
  case "$pkgman" in
    "homebrew")
      brew install libpq > $out
      # Force link libpq (keg-only formula) to make psql available in $PATH
      brew link --force libpq > $out 2>&1
      ;;
    "apt")
      sudo apt-get install -y postgresql-client > $out ;;
    *)
      echo "⚠️ Unsupported package manager: $pkgman"
      echo "  See: https://www.postgresql.org/download/"
      ;;
  esac
}

install_kubectl() {
  echo -e "\n🎛️  Installing kubectl..."
  case "$pkgman" in
    "homebrew")
      brew install kubectl > $out ;;
    "apt")
        # kubectl is not available on apt without first updating sources.
      sudo apt-get install -y kubectl > $out 2>&1 || (
        curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | \
          sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-archive-keyring.gpg > $out
        echo "deb [signed-by=/etc/apt/keyrings/kubernetes-archive-keyring.gpg] \
          https://apt.kubernetes.io/ kubernetes-xenial main" | \
          sudo tee /etc/apt/sources.list.d/kubernetes.list > $out
        sudo apt-get update > $out
        sudo apt-get install -y kubectl > $out
      )
      ;;
    *)
      echo "⚠️ Unsupported package manager: $pkgman"
      echo "  See: https://kubernetes.io/docs/tasks/tools/"
      ;;
  esac
}

install_awscli() {
  echo -e "\n☁️  Installing awscli..."
  case "$pkgman" in
    "homebrew")
      brew install awscli > $out ;;
    "apt")
      # NB: apt awscli is still v1; manually install v2
      curl "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" \
        -o "/tmp/awscliv2.zip" > $out
      unzip -qo /tmp/awscliv2.zip -d /tmp/awscliv2 > $out
      sudo /tmp/awscliv2/aws/install --update > $out
      rm -rf "/tmp/awscliv2*" > $out
      ;;
    *)
      echo "⚠️ Unsupported package manager: $pkgman"
      echo "  See: https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html"
      ;;
  esac
}

install_pulumi() {
  echo -e "\n🏗️  Installing pulumi..."
  case "$pkgman" in
    "homebrew")
      brew install pulumi > $out ;;
    "apt")
      (curl -fsSL https://get.pulumi.com | sh) > $out 2>&1
      # Fix path for current shell. Pulumi installer updates .<shell>rc so
      # next tab/window will already have pulumi in $PATH.
      export PATH="$PATH:$HOME/.pulumi/bin"
      ;;
    *)
      echo "⚠️ Unsupported package manager: $pkgman"
      echo "  See: https://www.pulumi.com/docs/get-started/install/"
      ;;
  esac
}

install_poetry() {
  echo -e "\n📚 Installing poetry..."
  # Install poetry
  (curl -sSL https://install.python-poetry.org | python3 -) > $out 2>&1
  # Check if poetry command is executable; if not, add it to path
  if ! command -v "poetry" >/dev/null 2>&1; then
    # When $POETRY_HOME is set, bin file lives at $POETRY_HOME/bin
    # Otherwise it lives at $HOME/.local/bin; see step 3 of:
    # https://python-poetry.org/docs/#installing-with-the-official-installer
    if [ -n "$POETRY_HOME" ]; then
      PATH="$POETRY_HOME/bin:$PATH"
      poetry_bin_path="$POETRY_HOME/bin"
    else
      PATH="$HOME/.local/bin:$PATH"
      poetry_bin_path="$HOME/.local/bin"
    fi
  fi
  # Install poe (task runner plugin)
  poetry self add 'poethepoet[poetry_plugin]' > $out
  # Install dependency upgrade plugin
  poetry self add 'poetry-plugin-up' > $out
}

if [[ "$yes" != true ]]; then
  echo -e "
ℹ️  This setup script will:

  🐙 Install git
  🌐 Install curl
  🔧 Install jq
  🐍 Install python 3.11
  🐳 Install docker
  🐘 Install psql
  🎛️  Install kubectl
  ☁️  Install awscli
  🏗️  Install pulumi
  📚 Install poetry
  📦 Install python dependencies
"
  if [[ "$verbose" != true ]]; then
    echo -e "(To see installation output, run with -v)\n"
  fi
  read -p "Proceed? (y/n) " -n 1 -r
  echo ""
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
      echo "Okbai 👋"
      exit 1
  fi
fi

update_packages
install_git
install_curl
install_jq
install_python
install_psql
install_docker
install_kubectl
install_awscli
install_pulumi
install_poetry

# https://github.com/pathbird/poetry-kernel
pip3 install --user poetry-kernel

# Copy .env-template to .env, stripping comments and empty lines
sed '/^#/d; /^$/d' dev/.env-template > .env
# Copy feature-flags-template.yaml to feature-flags.yaml,
# stripping comments and empty lines
sed '/^#/d; /^$/d' dev/feature-flags-template.yaml > feature-flags.yaml

echo -e "\n⚙️  Installing dependencies..."
rm -rf .venv
poetry env use $(which python3.11) > $out
poetry install --with test,dev,infra > $out
poetry run pre-commit install

echo "
🎉 All Done! Next steps:

  🥾 Bootstrap dependencies (run migrations, create S3 buckets, etc.):
      poetry bootstrap

  📖 More information:
      poetry info
"

if [ -n "${poetry_bin_path+set}" ]; then
  echo "  ⚠️  poetry was installed to $poetry_bin_path, which is not on your PATH env var.
      Add 'export PATH=\"\$PATH:$poetry_bin_path\"' to your shell config file.
"
fi
