"""Turn generated selfhosted-llm exercises into tasks in this suite.

The upstream repo already emits Harbor tasks, so this does not re-do that work.
What it changes is the three things that make them fit here:

  * they build FROM python:3.12-slim, which shares no layers with our base and so
    stores the harness block again per task;
  * the Terraform ones hardcode linux_amd64, and the workers are Graviton;
  * the generator withholds solutions, so `just calibrate` has no oracle -- the
    exercise solutions exist upstream and get embedded into solve.sh here.

Usage:
    python ops/scripts/adapt_selfhosted.py --src /tmp/generated \\
        --exercises <clone>/custom-benchmark/exercises --only 01-... 23-...
"""

from __future__ import annotations

import argparse
import base64
import shutil
from pathlib import Path

BASE = "hmdyb-task-base:1"

# Names carry the exercise number upstream. Keep the descriptive half: task ids
# show up on the dashboard and in job directories.
PREFIX = "selfhosted__"


def clean_name(generated: str) -> str:
    stem = generated[len(PREFIX):] if generated.startswith(PREFIX) else generated
    number, _, rest = stem.partition("-")
    return rest or stem


def rewrite_dockerfile(text: str) -> str:
    text = text.replace("FROM python:3.12-slim", f"FROM {BASE}")

    # Graviton: the upstream download and mirror are both amd64-only.
    text = text.replace(
        'curl -fsSL -o /tmp/tofu.zip \\\n      "https://github.com/opentofu/opentofu/releases/download/v1.11.4/tofu_1.11.4_linux_amd64.zip"',
        'ARCH="$(dpkg --print-architecture)" \\\n && curl -fsSL -o /tmp/tofu.zip \\\n      "https://github.com/opentofu/opentofu/releases/download/v1.11.4/tofu_1.11.4_linux_${ARCH}.zip"',
    )
    text = text.replace(
        "tofu providers mirror -platform=linux_amd64 /opt/tf-mirror",
        'tofu providers mirror -platform="linux_$(dpkg --print-architecture)" /opt/tf-mirror',
    )

    # curl stays: the harnesses installed in the base need it at run time.
    text = text.replace(
        " && apt-get purge -y curl unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*",
        " && rm -rf /var/lib/apt/lists/*",
    )
    return text


def solve_script(solution_dir: Path) -> str:
    """Embed the upstream solution, so the oracle needs nothing beside it."""
    lines = [
        "#!/bin/bash",
        "# The upstream reference answer, embedded file by file.",
        "set -euo pipefail",
        "",
    ]
    for path in sorted(solution_dir.rglob("*")):
        if not path.is_file():
            continue
        target = Path("/app") / path.relative_to(solution_dir)
        raw = path.read_bytes()
        try:
            text = raw.decode()
        except UnicodeDecodeError:
            # The plotting exercises ship expected images alongside the code.
            encoded = base64.b64encode(raw).decode()
            lines += [
                f"mkdir -p {target.parent}",
                f"base64 -d > {target} <<'SOLUTION_EOF'",
                "\n".join(encoded[i : i + 100] for i in range(0, len(encoded), 100)),
                "SOLUTION_EOF",
                "",
            ]
            continue
        lines += [
            f"mkdir -p {target.parent}",
            f"cat > {target} <<'SOLUTION_EOF'",
            text.rstrip("\n"),
            "SOLUTION_EOF",
            "",
        ]
    return "\n".join(lines)


def adapt(generated: Path, exercises: Path, out_root: Path) -> str | None:
    name = clean_name(generated.name)
    exercise = exercises / generated.name[len(PREFIX):]
    solution = exercise / "solution"
    if not solution.is_dir():
        print(f"  skip {name}: no upstream solution to calibrate against")
        return None

    dest = out_root / name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(generated, dest)

    dockerfile = dest / "environment" / "Dockerfile"
    dockerfile.write_text(rewrite_dockerfile(dockerfile.read_text()))

    (dest / "solution").mkdir(exist_ok=True)
    script = dest / "solution" / "solve.sh"
    script.write_text(solve_script(solution))
    script.chmod(0o755)

    toml = dest / "task.toml"
    text = toml.read_text().replace(
        f'name = "selfhosted/{generated.name[len(PREFIX):]}"', f'name = "hackathon/{name}"'
    )
    if "memory_mb" not in text:
        text = text.replace("os = \"linux\"", "os = \"linux\"\ncpus = 2\nmemory_mb = 4096")
    toml.write_text(text)
    return name


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--exercises", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("tasks"))
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()

    wanted = set(args.only or [])
    done = []
    for generated in sorted(args.src.iterdir()):
        if not generated.is_dir():
            continue
        stem = generated.name[len(PREFIX):]
        if wanted and stem not in wanted:
            continue
        name = adapt(generated, args.exercises, args.out)
        if name:
            done.append(name)
            print(f"  adapted {name}")
    print(f"{len(done)} tasks adapted")


if __name__ == "__main__":
    main()
