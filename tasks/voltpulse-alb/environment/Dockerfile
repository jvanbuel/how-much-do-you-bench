FROM hmdyb-task-base:1

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

# A git repo, because a harness that curates its own file list has no other way
# to find them: aider's context comes from its repo map, the repo map comes from
# git, and without one it asks the user to paste the code. Cloning the real
# repository here would hand the agent tests/ and solution/solve.sh, so the
# fixtures get their own single commit instead -- same discovery, no answers, no
# history to mine.
RUN cd /app \
 && git init -q \
 && git config user.email fixtures@example.com \
 && git config user.name fixtures \
 && git add -A \
 && git commit -qm "task fixtures"
