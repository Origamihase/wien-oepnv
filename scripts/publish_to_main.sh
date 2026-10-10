#!/usr/bin/env bash
# Commit what a workflow produced and push it to its branch: the one publish
# path of every workflow that writes to ``main``.
#
# Used by update-cycle.yml, build-feed.yml, seo-guard.yml,
# update-stations.yml and manual-full-refresh.yml. Before 2026-10-10 each of
# them pushed its own way; two failure classes followed from that:
#
#   * History loss. update-cycle.yml and build-feed.yml pushed with
#     ``--force-with-lease``. The ``git pull --rebase`` between two attempts
#     fetches ``origin/<branch>`` and so re-arms the lease on the very commit
#     it should protect (the mechanism that dropped PR #1783 from ``main`` on
#     2026-09-11, docs/archive/audits/audit-2026-09-12-force-push-history-loss.md).
#     Whenever that rebase could not be finished and was aborted, the next
#     attempt force-pushed the old base over every commit that had landed in
#     between, a merged PR included. ``main`` has no branch protection.
#   * No retry. seo-guard.yml, update-stations.yml and manual-full-refresh.yml
#     pushed once through ``stefanzweifel/git-auto-commit-action``; a commit
#     landing a second earlier rejected it (SEO Verify, 2026-10-04 17:27 UTC,
#     "cannot lock ref 'refs/heads/main'"), the run went red and its later
#     checks never ran. Being a ``uses:`` action it is also one more download
#     that can fail before any step runs (update-cycle.yml, 2026-05-26).
#
# So: a PLAIN push, never a force. After a successful rebase it is a
# fast-forward; in every other case it is rejected and the remote stays as
# it is. On a rejection the script re-syncs with ``git pull --rebase`` and
# tries again (4 attempts, 2/4/8 s apart). A rebase conflict resolves to the
# locally-built copy (the commit being replayed, "theirs" in rebase terms):
# the callers only commit files they regenerate. ``merge=union`` CSV ledgers
# never conflict. A rebase that still cannot finish is aborted and the next
# attempt starts over.
#
# Usage:
#   scripts/publish_to_main.sh --message MSG --label TITLE [--strict]
#       [--validate-feeds] (--all | PATH...)
#
#   --message         commit message
#   --label           title of the ``::warning`` annotations
#   --all             stage everything (``git add -A``); otherwise only the
#                     listed paths that exist
#   --validate-feeds  skip the publish (warning, exit 0) when docs/feed.xml or
#                     docs/feed.en.xml is empty or not well-formed XML, so a
#                     builder hiccup never pushes over the last good feed
#   --strict          exit 1 when every attempt failed (default: warning and
#                     exit 0, for runs whose output the next run rebuilds)
#
# The branch is ``$GITHUB_REF_NAME`` (override with ``PUBLISH_BRANCH``); the
# first back-off is 2 s (``PUBLISH_RETRY_DELAY``, for the tests).

# Intentionally no ``-e``: every failure below is handled explicitly.
set -uo pipefail

message=""
label="publish"
strict=0
validate_feeds=0
add_all=0
paths=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --message) message=$2; shift 2 ;;
    --label) label=$2; shift 2 ;;
    --strict) strict=1; shift ;;
    --validate-feeds) validate_feeds=1; shift ;;
    --all) add_all=1; shift ;;
    --) shift; paths+=("$@"); break ;;
    -*) echo "publish_to_main.sh: unknown option $1" >&2; exit 2 ;;
    *) paths+=("$1"); shift ;;
  esac
done
if [ -z "${message}" ]; then
  echo "publish_to_main.sh: --message is required" >&2
  exit 2
fi
if [ "${add_all}" -eq 0 ] && [ "${#paths[@]}" -eq 0 ]; then
  echo "publish_to_main.sh: give --all or at least one path" >&2
  exit 2
fi
branch="${PUBLISH_BRANCH:-${GITHUB_REF_NAME:-}}"
if [ -z "${branch}" ]; then
  echo "publish_to_main.sh: no branch (GITHUB_REF_NAME / PUBLISH_BRANCH)" >&2
  exit 2
fi

# Keep ``git rebase --continue`` non-interactive, and abort a stalled
# transfer (< 1 KB/s for 30 s) instead of riding it to the job's time limit.
export GIT_EDITOR=true
export GIT_HTTP_LOW_SPEED_LIMIT="${GIT_HTTP_LOW_SPEED_LIMIT:-1000}"
export GIT_HTTP_LOW_SPEED_TIME="${GIT_HTTP_LOW_SPEED_TIME:-30}"

# actions/checkout sets no committer identity.
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

# 1) Never ship a malformed or empty feed.
if [ "${validate_feeds}" -eq 1 ]; then
  for feed in docs/feed.xml docs/feed.en.xml; do
    [ -f "${feed}" ] || continue
    if [ ! -s "${feed}" ]; then
      echo "::warning title=feed-validate::${feed} is empty — skipping publish (last good feed stays online)."
      exit 0
    fi
    if ! python -c "import sys, xml.etree.ElementTree as ET; ET.parse(sys.argv[1])" "${feed}"; then
      echo "::warning title=feed-validate::${feed} is not well-formed XML — skipping publish (last good feed stays online)."
      exit 0
    fi
  done
fi

# 2) Stage + commit.
if [ "${add_all}" -eq 1 ]; then
  git add -A
else
  for path in "${paths[@]}"; do
    [ -e "${path}" ] && git add -- "${path}"
  done
fi
if git diff --cached --quiet; then
  echo "No changes — nothing to commit or push."
  exit 0
fi
git commit -q -m "${message}"

# 3) Plain push with retry and re-sync. Never --force / --force-with-lease.
max_attempts=4
delay="${PUBLISH_RETRY_DELAY:-2}"
attempt=1
while :; do
  if git push origin "HEAD:${branch}"; then
    echo "Published on attempt ${attempt}/${max_attempts}."
    exit 0
  fi
  if [ "${attempt}" -ge "${max_attempts}" ]; then
    echo "::warning title=${label}::git push failed after ${max_attempts} attempts; nothing was published, the remote is unchanged."
    [ "${strict}" -eq 1 ] && exit 1
    exit 0
  fi
  echo "Push attempt ${attempt}/${max_attempts} failed; re-syncing with origin/${branch} and retrying in ${delay}s."
  sleep "${delay}"
  if ! git pull --rebase --autostash origin "${branch}"; then
    mapfile -t -d '' conflicts < <(git diff --name-only --diff-filter=U -z)
    if [ "${#conflicts[@]}" -gt 0 ]; then
      echo "::warning title=${label}::rebase conflict on a concurrent push; keeping the locally built copies of: ${conflicts[*]}"
      git checkout --theirs -- "${conflicts[@]}"
      git add -- "${conflicts[@]}"
      git rebase --continue || git rebase --abort || true
    else
      # The fetch itself failed, or the rebase stopped for another reason:
      # drop the half-done rebase; the next attempt starts over.
      git rebase --abort 2>/dev/null || true
    fi
  fi
  attempt=$((attempt + 1))
  delay=$((delay * 2))
done
