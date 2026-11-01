#!/usr/bin/env bash
# Commit what a workflow produced and push it to its branch: the one publish
# path of every workflow that writes to ``main``.
#
# Used by update-cycle.yml, build-feed.yml, seo-guard.yml,
# update-stations.yml and manual-full-refresh.yml, since 2026-11-01 also by
# ki-waechter.yml. Before 2026-10-10 the first five each pushed their own
# way; two failure classes followed from that:
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
# locally built lines (the commit being replayed, "theirs" in rebase terms)
# and keeps every other line of the concurrent commit (``keep_local_lines``):
# a file the run regenerates may also be edited by hand (README.md, the
# stations). ``merge=union`` CSV ledgers never conflict. A rebase that
# still cannot finish is aborted and the next attempt starts over.
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
# Every staged page under docs/ that holds a Liquid pair is made inert
# before the commit (step 2b).
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

# One interpreter for every check below (steps 1 and 2b): setup-python
# provides ``python``, a bare system often only ``python3``. Empty when
# neither is on PATH.
python_bin="$(command -v python || command -v python3 || true)"

# 1) Never ship a malformed or empty feed.
if [ "${validate_feeds}" -eq 1 ]; then
  for feed in docs/feed.xml docs/feed.en.xml; do
    [ -f "${feed}" ] || continue
    if [ ! -s "${feed}" ]; then
      echo "::warning title=feed-validate::${feed} is empty — skipping publish (last good feed stays online)."
      exit 0
    fi
    if [ -z "${python_bin}" ]; then
      echo "::warning title=feed-validate::${feed} not checked: no python on PATH — skipping publish (last good feed stays online)."
      exit 0
    fi
    # Exit 3: the parser rejected the feed (an unknown declared encoding
    # included). Any other non-zero status: the interpreter did not run the
    # check (a broken install, a shim without a version), so the warning
    # says that instead of calling a good feed malformed.
    feed_status=0
    "${python_bin}" -c '
import sys
import xml.etree.ElementTree as ET
try:
    ET.parse(sys.argv[1])
except (ET.ParseError, LookupError, ValueError) as exc:
    print(f"{sys.argv[1]}: {exc}", file=sys.stderr)
    sys.exit(3)
' "${feed}" || feed_status=$?
    if [ "${feed_status}" -eq 3 ]; then
      echo "::warning title=feed-validate::${feed} is not well-formed XML — skipping publish (last good feed stays online)."
      exit 0
    elif [ "${feed_status}" -ne 0 ]; then
      echo "::warning title=feed-validate::${feed} not checked: ${python_bin} exit ${feed_status} — skipping publish (last good feed stays online)."
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

# 2b) Keep Liquid out of the Pages build. GitHub Pages runs Liquid over every
# page under docs/, and a commit pushed here starts the Pages build but no
# test run (a GITHUB_TOKEN push starts no workflow such as test.yml). The
# page of 2026-11-01 (#2092, a bare "{%" that failed every Pages build until
# #2108) came through a pull request, which the tests and since then
# pages-build-check.yml cover; this step covers the bot commits. The writers
# make their text inert themselves; every staged page that still holds a
# Liquid pair goes through liquid_safe (src/utils/liquid.py, intact raw
# blocks kept) and is staged again, with a warning naming the file and
# lines. Paths travel NUL-separated, so any file name works and no argument
# list can grow too long. The index matches the working tree here: step 2
# staged from it just above.
mapfile -d '' -t staged_docs < <(git diff --cached --name-only -z --diff-filter=d -- docs/)
if [ "${#staged_docs[@]}" -gt 0 ]; then
  liquid_py="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/src/utils/liquid.py"
  liquid_out="$(mktemp)"
  liquid_status=1
  if [ -n "${python_bin}" ]; then
    printf '%s\0' "${staged_docs[@]}" \
      | "${python_bin}" "${liquid_py}" --fix --null --docs-root docs > "${liquid_out}"
    liquid_status=$?
  fi
  # Entries are written as each page is rewritten (for a symlink: its
  # target): stage them even when the check stopped halfway. Exit 2 means
  # liquid.py passed over a page it could not read and named it on stderr;
  # any other non-zero status means it did not run or stopped early and
  # named nothing beyond the entries.
  while IFS= read -r -d '' entry; do
    echo "::warning title=${label}::Liquid in ${entry%:*} (line ${entry##*:}) made inert before publishing, so the Pages build cannot stop on it."
    git add -- "${entry%:*}"
  done < "${liquid_out}"
  rm -f "${liquid_out}"
  if [ -z "${python_bin}" ]; then
    echo "::warning title=${label}::Liquid check skipped: no python on PATH; the staged pages under docs/ are published unchecked."
  elif [ "${liquid_status}" -eq 2 ]; then
    echo "::warning title=${label}::Liquid check passed over staged pages under docs/ it could not read (liquid.py names them in the log); those are published unchecked."
  elif [ "${liquid_status}" -ne 0 ]; then
    echo "::warning title=${label}::Liquid check stopped (liquid.py exit ${liquid_status}); staged pages under docs/ not named above may be published unchecked."
  fi
fi
git commit -q -m "${message}"

# Resolve one conflicted path of a stopped rebase. Only the conflicting
# lines take the locally built side ("theirs" in rebase terms); every other
# change of the concurrent commit stays. Until 2026-10-11 the whole file was
# replaced by the local copy: a README edit merged during an Update run next
# to the stats block, or a station added by hand to data/stations.json during
# the Stationspflege run, was reverted with it, and the run stayed green
# (Gesamtprüfung 11.10.2026). A path without all three versions (added or
# deleted on one side) or one ``git merge-file`` cannot merge (binary) keeps
# the whole local copy, as before.
keep_local_lines() {
  local path=$1 tmp rc
  tmp=$(mktemp -d)
  if git show ":1:${path}" >"${tmp}/base" 2>/dev/null \
    && git show ":2:${path}" >"${tmp}/upstream" 2>/dev/null \
    && git show ":3:${path}" >"${tmp}/local" 2>/dev/null; then
    git merge-file --theirs -q "${tmp}/upstream" "${tmp}/base" "${tmp}/local"
    rc=$?
    if [ "${rc}" -ge 0 ] && [ "${rc}" -lt 128 ]; then
      cp "${tmp}/upstream" "${path}"
      rm -rf "${tmp}"
      return 0
    fi
  fi
  rm -rf "${tmp}"
  git checkout --theirs -- "${path}"
}

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
      echo "::warning title=${label}::rebase conflict on a concurrent push; keeping the locally built lines of: ${conflicts[*]}"
      for conflict in "${conflicts[@]}"; do
        keep_local_lines "${conflict}"
      done
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
