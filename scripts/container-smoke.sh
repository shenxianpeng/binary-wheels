#!/bin/sh
# Smoke-test the wheels in dist/ inside a container (used by CI for musl and old glibc).
# Usage: docker run --rm -v "$PWD:/repo:ro" <python image> sh /repo/scripts/container-smoke.sh
set -eu
cp -r /repo/src /tmp/src
cd /repo
python -m pip install -q --root-user-action=ignore packaging
PYTHONPATH=/tmp/src python -c "import sys; from binwheel.cli import main; sys.exit(main(['smoke', '--dist', 'dist']))"
