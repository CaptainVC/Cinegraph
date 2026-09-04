#!/usr/bin/env bash

# Root-owned, no-argument wrapper for the isolated private-corpus receiver.
set -euo pipefail
PATH=/usr/sbin:/usr/bin
export PATH

readonly DEPLOY_ROOT="/opt/cinegraph"
readonly RELEASES_ROOT="$DEPLOY_ROOT/releases"
readonly CURRENT_LINK="$DEPLOY_ROOT/current"
readonly SHARED_ROOT="$DEPLOY_ROOT/shared"
readonly CORPUS_ROOT="$SHARED_ROOT/private-corpus"
readonly DEV_CORPUS_ROOT="$CORPUS_ROOT/dev"
readonly TRANSACTIONS_ROOT="$DEV_CORPUS_ROOT/transactions"
readonly OBJECTS_ROOT="$DEV_CORPUS_ROOT/objects"
readonly QUARANTINE_ROOT="$DEV_CORPUS_ROOT/quarantine"
readonly TRANSFER_LOCK="$DEV_CORPUS_ROOT/.transfer.lock"
readonly DEPLOYMENT_LOCK="$DEPLOY_ROOT/.deploy.lock"
readonly REPOSITORY_URL="https://github.com/CaptainVC/Cinegraph.git"
readonly TIMEOUT_SECONDS="300"
readonly KILL_AFTER_SECONDS="5"

fail() {
    printf '%s\n' "private corpus receiver rejected the request" >&2
    exit 1
}

check_root_path() {
    local path="$1"
    local kind="$2"
    local mode="$3"
    [[ ! -L "$path" ]] || fail
    if [[ "$kind" == "directory" ]]; then
        [[ -d "$path" ]] || fail
    else
        [[ -f "$path" ]] || fail
    fi
    [[ "$(stat -c '%u:%g' "$path")" == "0:0" ]] || fail
    [[ "$(stat -c '%a' "$path")" == "$mode" ]] || fail
}

[[ $EUID -eq 0 ]] || fail
[[ $# -eq 0 ]] || fail
[[ "${SUDO_USER-}" == "cinegraph-corpus" ]] || fail
[[ "$(uname -s)" == "Linux" ]] || fail
[[ "$(uname -m)" == "x86_64" ]] || fail
for command in env flock git id python3 readlink stat sudo timeout uname; do
    command -v "$command" >/dev/null 2>&1 || fail
done

check_root_path /opt directory 755
check_root_path /usr directory 755
check_root_path /usr/bin directory 755
check_root_path /usr/sbin directory 755
check_root_path /usr/local directory 755
check_root_path /usr/local/sbin directory 755
check_root_path "$DEPLOY_ROOT" directory 750
check_root_path "$RELEASES_ROOT" directory 750
check_root_path "$SHARED_ROOT" directory 750
check_root_path "$CORPUS_ROOT" directory 700
check_root_path "$DEV_CORPUS_ROOT" directory 700
check_root_path "$TRANSACTIONS_ROOT" directory 700
check_root_path "$OBJECTS_ROOT" directory 700
check_root_path "$QUARANTINE_ROOT" directory 700
check_root_path /usr/local/sbin/cinegraph-receive-private-corpus file 755

umask 077
# Lock order is invariant: corpus transfer first, deployment second. This keeps
# the current release and public catalogue fixed for the complete bounded receive.
[[ ! -L "$TRANSFER_LOCK" ]] || fail
exec 8>"$TRANSFER_LOCK"
[[ -f "$TRANSFER_LOCK" && "$(stat -c '%u:%g:%a:%h' "$TRANSFER_LOCK")" == "0:0:600:1" ]] || fail
flock -n 8 || fail
[[ ! -L "$DEPLOYMENT_LOCK" ]] || fail
exec 9>"$DEPLOYMENT_LOCK"
[[ -f "$DEPLOYMENT_LOCK" && "$(stat -c '%u:%g:%a:%h' "$DEPLOYMENT_LOCK")" == "0:0:600:1" ]] || fail
flock -w 10 9 || fail

[[ -L "$CURRENT_LINK" ]] || fail
release_dir="$(readlink -f -- "$CURRENT_LINK")"
[[ "$release_dir" =~ ^/opt/cinegraph/releases/[0-9a-f]{40}$ ]] || fail
check_root_path "$release_dir" directory "$(stat -c '%a' "$release_dir")"
[[ "$((8#$(stat -c '%a' "$release_dir") & 8#022))" -eq 0 ]] || fail
[[ -d "$release_dir/.git" && ! -L "$release_dir/.git" ]] || fail
[[ "$(git -C "$release_dir" remote)" == "origin" ]] || fail
[[ "$(git -C "$release_dir" remote get-url origin)" == "$REPOSITORY_URL" ]] || fail
[[ -z "$(git -C "$release_dir" status --porcelain=v1 --untracked-files=all)" ]] || fail
release_sha="$(git -C "$release_dir" rev-parse --verify HEAD)"
[[ "$release_sha" =~ ^[0-9a-f]{40}$ ]] || fail
[[ "$release_dir" == "$RELEASES_ROOT/$release_sha" ]] || fail
git -C "$release_dir" merge-base --is-ancestor \
    "$release_sha" refs/remotes/origin/main || fail

catalogue="$release_dir/knowledge/catalogue.json"
receiver="$release_dir/scripts/receive_private_corpus.py"
contract="$release_dir/scripts/private_corpus_host_contract.py"
check_root_path "$catalogue" file 644
check_root_path "$receiver" file "$(stat -c '%a' "$receiver")"
check_root_path "$contract" file "$(stat -c '%a' "$contract")"
[[ "$((8#$(stat -c '%a' "$receiver") & 8#022))" -eq 0 ]] || fail
[[ "$((8#$(stat -c '%a' "$contract") & 8#022))" -eq 0 ]] || fail
[[ "$(git -C "$release_dir" ls-files --error-unmatch -- scripts/receive_private_corpus.py)" == \
    "scripts/receive_private_corpus.py" ]] || fail
[[ "$(git -C "$release_dir" ls-files --error-unmatch -- scripts/private_corpus_host_contract.py)" == \
    "scripts/private_corpus_host_contract.py" ]] || fail

exec env -i PATH=/usr/sbin:/usr/bin \
    timeout --signal=TERM --kill-after="${KILL_AFTER_SECONDS}s" "${TIMEOUT_SECONDS}s" \
    python3 -I -S -B "$receiver"
