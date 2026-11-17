#!/usr/bin/env bash
# Publish this repo's committed `main` tree to the public copy as one new commit (decision 39).
# The public copy never receives this repo's history, so old commits (F6) stay private.
# First run also publishes the archived results as a one-commit branch of the same name.
#
#   scripts/publish_public.sh            # publish
#   DRY_RUN=1 scripts/publish_public.sh  # build and check the tree, push nothing
set -euo pipefail

PUBLIC_REPO="${PUBLIC_REPO:-aatif101/assemblyai-200-not-done}"
[[ "$PUBLIC_REPO" =~ ^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$ ]] || { echo "PUBLIC_REPO must be owner/name, got: $PUBLIC_REPO"; exit 1; }
ARCHIVE_BRANCH="archive/all-runs-2026-11-16"
ARCHIVE_PATHS=(
  results/phase2/pilot_v1 results/phase2/pilot_discarded results/phase2/pilot results/phase2/runs
  results/phase2/codex/pilot_gpt-6-astra results/phase2/codex/runs_set_aside_usage_limit
  results/phase3/judge_v1_t4_without_readme
)
DESCRIPTION="How often coding agents ship AssemblyAI integrations that run clean but don't do what was asked, graded by what the API actually ran."

cd "$(git rev-parse --show-toplevel)"
PY=$(command -v python3 || command -v python)
[ -x .venv/Scripts/python.exe ] && PY="$PWD/.venv/Scripts/python.exe"
[ -x .venv/bin/python ] && PY="$PWD/.venv/bin/python"
[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || { echo "check out main first"; exit 1; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "commit your changes first: only committed files are published"; exit 1; }
SRC=$(git rev-parse --short HEAD)

WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT

# Refuses to publish a tree holding a credential or an account identifier.
check_tree() {
  "$PY" scripts/check_secrets.py --root "$1" || { echo "secret check failed in $1"; exit 1; }
  if grep -rIlE '"(project_id|token_id)"[[:space:]]*:[[:space:]]*"?[0-9]' "$1" --exclude-dir=.git --exclude=test_account_ids.py; then
    echo "account identifiers found (F6): not publishing"; exit 1
  fi
}

# main
mkdir "$WORK/main"
git archive HEAD | tar -x -C "$WORK/main"
check_tree "$WORK/main"
(cd "$WORK/main" && "$PY" -m pytest -q -p no:cacheprovider >/dev/null) || { echo "tests fail on the exported tree"; exit 1; }

# archived results: checked on every run, pushed the first time only
mkdir "$WORK/archive"
git archive "origin/$ARCHIVE_BRANCH" "${ARCHIVE_PATHS[@]}" | tar -x -C "$WORK/archive"
printf '%s\n' "# Archived runs" "" \
  'These runs are not behind the published numbers. What each folder is, and why it was set aside: `results/ARCHIVE.md` on `main`.' \
  > "$WORK/archive/README.md"
check_tree "$WORK/archive"

exists=1; gh repo view "$PUBLIC_REPO" >/dev/null 2>&1 || exists=0
if [ "${DRY_RUN:-0}" = 1 ]; then echo "dry run: main ($SRC) and archive trees checked, public repo exists=$exists, nothing pushed"; exit 0; fi

if [ $exists = 0 ]; then
  gh repo create "$PUBLIC_REPO" --public --description "$DESCRIPTION" --disable-wiki
fi
URL="https://github.com/$PUBLIC_REPO.git"

(
  cd "$WORK/main"
  git init -q -b main
  if git ls-remote --exit-code "$URL" refs/heads/main >/dev/null 2>&1; then
    git fetch -q --depth 1 "$URL" main && git reset -q --soft FETCH_HEAD
  fi
  git add -A
  if git rev-parse -q --verify HEAD >/dev/null && git diff --cached --quiet; then
    echo "public main already matches $SRC"
  else
    if git rev-parse -q --verify HEAD >/dev/null; then MSG="Update from source $SRC"; else MSG="200 ≠ done: AssemblyAI agent integrations, graded by what the API ran"; fi
    git commit -q -m "$MSG" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
    git push -q "$URL" HEAD:main
    echo "pushed main ($MSG)"
  fi
)

# archive branch, first time only
if ! git ls-remote --exit-code "$URL" "refs/heads/$ARCHIVE_BRANCH" >/dev/null 2>&1; then
  (
    cd "$WORK/archive"
    git init -q -b "$ARCHIVE_BRANCH"
    git add -A
    git commit -q -m "Archived runs, not behind the published numbers (see results/ARCHIVE.md on main)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
    git push -q "$URL" "HEAD:refs/heads/$ARCHIVE_BRANCH"
    echo "pushed $ARCHIVE_BRANCH"
  )
fi
echo "public copy: https://github.com/$PUBLIC_REPO"
