#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# release.sh — GitFlow release of avela_tuner
#
# Usage:
#   cicd/release.sh <version>          e.g. cicd/release.sh 0.1.0
#   cicd/release.sh major|minor|patch  bumps the corresponding component
#
# Flow:
#   devel → release/X.Y.Z → main (tag vX.Y.Z) → devel (X.Y.(Z+1).dev0)
#
# Version source: pyproject.toml. Pushes main, devel and the tag to origin at the end.
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYPROJECT="$REPO_ROOT/pyproject.toml"

die() { echo "ERROR: $*" >&2; exit 1; }

current_version() { grep -m1 '^version = ' "$PYPROJECT" | sed 's/version = "\(.*\)"/\1/'; }
bare_version() { echo "$1" | grep -oP '^\d+\.\d+\.\d+'; }

bump() {
    local cur; cur="$(current_version)"
    local major minor patch
    IFS='.' read -r major minor patch <<< "$(bare_version "$cur")"
    case "$1" in
        major) echo "$((major + 1)).0.0" ;;
        minor) echo "$major.$((minor + 1)).0" ;;
        # X.Y.Z.dev0 already targets X.Y.Z
        patch) if [[ "$cur" == *".dev"* ]]; then echo "$major.$minor.$patch"; else echo "$major.$minor.$((patch + 1))"; fi ;;
    esac
}

next_dev() {
    local major minor patch
    IFS='.' read -r major minor patch <<< "$(bare_version "$1")"
    echo "$major.$minor.$((patch + 1)).dev0"
}

set_version() { sed -i "s/^version = \".*\"/version = \"$1\"/" "$PYPROJECT"; }

[[ $# -eq 1 ]] || die "Usage: $0 <version|major|minor|patch>"
case "$1" in
    major|minor|patch) TARGET_VERSION="$(bump "$1")" ;;
    [0-9]*) TARGET_VERSION="$1"; bare_version "$TARGET_VERSION" > /dev/null || die "Version must be X.Y.Z" ;;
    *) die "Argument must be a version (X.Y.Z) or one of: major minor patch" ;;
esac
DEV_VERSION="$(next_dev "$TARGET_VERSION")"
RELEASE_BRANCH="release/$TARGET_VERSION"
TAG="v$TARGET_VERSION"

cd "$REPO_ROOT"
[[ -z "$(git status --porcelain)" ]] || die "Working tree is dirty. Commit or stash changes before releasing."
[[ "$(git rev-parse --abbrev-ref HEAD)" == "devel" ]] || die "Must be on 'devel' to start a release."
git fetch --quiet origin
git diff --quiet devel origin/devel || die "Local 'devel' is not in sync with origin/devel. Pull first."
git tag | grep -qx "$TAG" && die "Tag $TAG already exists."

echo "Releasing $TARGET_VERSION  (devel will become $DEV_VERSION)"
git checkout -b "$RELEASE_BRANCH"
set_version "$TARGET_VERSION"
git add "$PYPROJECT"
git commit -m "chore: set version $TARGET_VERSION"
# the checks on a fresh clone of the release commit, never on this working copy
if ! "$REPO_ROOT/cicd/test_unit.sh" --source "$REPO_ROOT" --tag "$RELEASE_BRANCH"; then
    git checkout --quiet devel
    git branch -D "$RELEASE_BRANCH"
    die "The checks failed on $TARGET_VERSION; nothing was released."
fi
git tag -a "$TAG" -m "$TAG"

git checkout main
git pull --quiet origin main
git merge --no-ff "$RELEASE_BRANCH" -m "release: $TARGET_VERSION"

git checkout devel
git merge --no-ff "$RELEASE_BRANCH" -m "release: merge $TARGET_VERSION back to devel"
set_version "$DEV_VERSION"
git add "$PYPROJECT"
git commit -m "chore: bump devel to $DEV_VERSION"
git branch -d "$RELEASE_BRANCH"

git push origin main devel "$TAG"
echo "Done. Released $TAG. devel is now $DEV_VERSION."
