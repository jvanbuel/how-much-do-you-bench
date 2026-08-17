FROM ghcr.io/jvanbuel/hmdyb-task-base:1

WORKDIR /app

RUN pip install --no-cache-dir pytest

# OpenTofu (direct release download — robust on slim).
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates unzip \
 && ARCH="$(dpkg --print-architecture)" \
 && curl -fsSL -o /tmp/tofu.zip \
      "https://github.com/opentofu/opentofu/releases/download/v1.11.4/tofu_1.11.4_linux_${ARCH}.zip" \
 && unzip -o /tmp/tofu.zip tofu -d /usr/local/bin \
 && rm -f /tmp/tofu.zip \
 && rm -rf /var/lib/apt/lists/* \
 && ln -s tofu /usr/local/bin/terraform \
 && tofu version

# Bake the required providers into an offline filesystem mirror, then point
# provider installation at it so `tofu init` during verification needs no network.
RUN mkdir -p /opt/tf-mirror /tmp/mg \
 && printf 'terraform {\n  required_providers {\n    aws = { source = "hashicorp/aws", version = "~> 5.0" }\n  }\n}\n' > /tmp/mg/versions.tf \
 && cd /tmp/mg && tofu providers mirror -platform="linux_$(dpkg --print-architecture)" /opt/tf-mirror \
 && rm -rf /tmp/mg
RUN printf 'provider_installation {\n  filesystem_mirror {\n    path    = "/opt/tf-mirror"\n    include = ["*/*"]\n  }\n}\n' > /etc/tofurc
ENV TF_CLI_CONFIG_FILE=/etc/tofurc

# The starter workspace the agent edits, copied from this build context.
COPY app/ /app/
