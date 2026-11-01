#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# build.sh — the avela_tuner wheel and sdist from origin
#
# Usage:
#   cicd/build.sh                    build from BUILD_BRANCH (cicd/.env, default main)
#   cicd/build.sh --tag v0.1.0       build from a tag
#   cicd/build.sh --version 0.1.0    build from tag v0.1.0
#
# Always from a fresh shallow clone in $CLONES_DIR/build-<time>, never the working copy (removed afterwards). Output: $ARTIFACTS_DIR/<version>/ with the wheel, the
# sdist, checksums.sha256 and version.txt (made here, never committed), and a `latest` symlink. Install a build with
#   pipx install $ARTIFACTS_DIR/latest/avela_tuner-<version>-py3-none-any.whl
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
die() { echo "ERROR: $*" >&2; exit 1; }
export PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring

[[ -f "$REPO_ROOT/cicd/.env" ]] && { set -a; source "$REPO_ROOT/cicd/.env"; set +a; }
: "${BUILD_DIR:=$REPO_ROOT/build}"
: "${ARTIFACTS_DIR:=$BUILD_DIR/artifacts}"
: "${CLONES_DIR:=$BUILD_DIR/clones}"
: "${REPO_URL:=$(git -C "$REPO_ROOT" remote get-url origin 2> /dev/null || true)}"
: "${BUILD_BRANCH:=main}"
[[ -n "$REPO_URL" ]] || die "No REPO_URL (cicd/.env) and no origin remote"

REF="$BUILD_BRANCH"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --tag) [[ $# -ge 2 ]] || die "--tag requires a value"; REF="$2"; shift 2 ;;
        --version) [[ $# -ge 2 ]] || die "--version requires a value"; REF="v$2"; shift 2 ;;
        *) die "Unknown argument: $1. Usage: $0 [--tag vX.Y.Z | --version X.Y.Z]" ;;
    esac
done

mkdir -p "$CLONES_DIR"
CLONE_DIR="$CLONES_DIR/build-$(date +%Y%m%d-%H%M%S)"
OUT_DIR=""
BUILD_OK=false
trap 'rm -rf "$CLONE_DIR"; $BUILD_OK || [[ -z "$OUT_DIR" ]] || rm -rf "$OUT_DIR"' EXIT
echo "Cloning $REPO_URL ($REF) → $CLONE_DIR"
git clone --branch "$REF" --depth 1 --quiet "$REPO_URL" "$CLONE_DIR"

VERSION=$(grep -m1 '^version = ' "$CLONE_DIR/pyproject.toml" | sed 's/version = "\(.*\)"/\1/')
[[ -n "$VERSION" ]] || die "Could not read the version from pyproject.toml"
OUT_DIR="$ARTIFACTS_DIR/$VERSION"
if [[ -d "$OUT_DIR" ]]; then
    if [[ -t 0 ]]; then
        read -r -p "Build $VERSION already exists at $OUT_DIR. Delete and rebuild? [y/N] " confirm
        [[ "$confirm" =~ ^[Yy]$ ]] || { OUT_DIR=""; die "Aborted."; }
    fi
    rm -rf "$OUT_DIR"
fi
mkdir -p "$OUT_DIR"

(cd "$CLONE_DIR" && poetry build --quiet --output "$OUT_DIR")
(cd "$OUT_DIR" && sha256sum -- * > checksums.sha256)
printf '%s\n' "$VERSION" > "$OUT_DIR/version.txt"
ln -sfn "$VERSION" "$ARTIFACTS_DIR/latest"
BUILD_OK=true
echo "Built $VERSION → $OUT_DIR"
echo "Upload to the GitHub release v$VERSION:"
(cd "$OUT_DIR" && ls -1 ./*.whl ./*.tar.gz checksums.sha256 | sed "s|^\./||; s|^|  $OUT_DIR/|")
ls -1 "$OUT_DIR"
