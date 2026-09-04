#!/usr/bin/env bash

# Root-owned, no-argument wrapper for one exact private-corpus processing request.
set -euo pipefail
PATH=/usr/sbin:/usr/bin
export PATH
GIT_CONFIG_GLOBAL=/dev/null
GIT_CONFIG_SYSTEM=/dev/null
GIT_TERMINAL_PROMPT=0
export GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM GIT_TERMINAL_PROMPT

readonly DEPLOY_ROOT="/opt/cinegraph"
readonly RELEASES_ROOT="$DEPLOY_ROOT/releases"
readonly CURRENT_LINK="$DEPLOY_ROOT/current"
readonly SHARED_ROOT="$DEPLOY_ROOT/shared"
readonly CORPUS_ROOT="$SHARED_ROOT/private-corpus"
readonly DEV_CORPUS_ROOT="$CORPUS_ROOT/dev"
readonly TRANSACTIONS_ROOT="$DEV_CORPUS_ROOT/transactions"
readonly OBJECTS_ROOT="$DEV_CORPUS_ROOT/objects"
readonly QUARANTINE_ROOT="$DEV_CORPUS_ROOT/quarantine"
readonly PROCESSING_ROOT="$DEV_CORPUS_ROOT/processing"
readonly PROCESSING_RECEIPTS_ROOT="$PROCESSING_ROOT/receipts"
readonly TRANSFER_LOCK="$DEV_CORPUS_ROOT/.transfer.lock"
readonly DEPLOYMENT_LOCK="$DEPLOY_ROOT/.deploy.lock"
readonly PROCESSING_LOCK="$DEV_CORPUS_ROOT/.processing.lock"
readonly ENV_FILE="/etc/cinegraph/dev.env"
readonly REPOSITORY_URL="https://github.com/CaptainVC/Cinegraph.git"
readonly TIMEOUT_SECONDS="1800"
readonly KILL_AFTER_SECONDS="10"

fail() {
    printf '%s\n' "private corpus processing rejected the request" >&2
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
for command in docker env flock git id python3 readlink stat timeout uname; do
    command -v "$command" >/dev/null 2>&1 || fail
done
docker compose version >/dev/null 2>&1 || fail

check_root_path /opt directory 755
check_root_path /etc directory 755
check_root_path /etc/cinegraph directory 700
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
check_root_path "$PROCESSING_ROOT" directory 700
check_root_path "$PROCESSING_RECEIPTS_ROOT" directory 700
check_root_path "$ENV_FILE" file 600
check_root_path /usr/local/sbin/cinegraph-process-private-corpus file 755

umask 077
# Lock order is invariant across transfer, deploy, and processing helpers.
[[ ! -L "$TRANSFER_LOCK" ]] || fail
exec 8>"$TRANSFER_LOCK"
[[ -f "$TRANSFER_LOCK" && "$(stat -c '%u:%g:%a:%h' "$TRANSFER_LOCK")" == "0:0:600:1" ]] || fail
flock -n 8 || fail
[[ ! -L "$DEPLOYMENT_LOCK" ]] || fail
exec 9>"$DEPLOYMENT_LOCK"
[[ -f "$DEPLOYMENT_LOCK" && "$(stat -c '%u:%g:%a:%h' "$DEPLOYMENT_LOCK")" == "0:0:600:1" ]] || fail
flock -w 10 9 || fail
[[ ! -L "$PROCESSING_LOCK" ]] || fail
exec 7>"$PROCESSING_LOCK"
[[ -f "$PROCESSING_LOCK" && "$(stat -c '%u:%g:%a:%h' "$PROCESSING_LOCK")" == "0:0:600:1" ]] || fail
flock -n 7 || fail

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
[[ "$(git -C "$release_dir" rev-parse --verify refs/remotes/origin/main)" == "$release_sha" ]] || fail

catalogue="$release_dir/knowledge/catalogue.json"
processor="$release_dir/scripts/run_private_corpus_processing.py"
worker="$release_dir/scripts/ingest_private_corpus_workspace.py"
host_contract="$release_dir/scripts/private_corpus_host_contract.py"
processing_contract="$release_dir/scripts/private_corpus_processing_contract.py"
receiver="$release_dir/scripts/receive_private_corpus.py"
compose="$release_dir/deploy/compose.yaml"
check_root_path "$catalogue" file 644
for tracked_file in "$processor" "$worker" "$host_contract" "$processing_contract" "$receiver" "$compose"; do
    check_root_path "$tracked_file" file "$(stat -c '%a' "$tracked_file")"
    [[ "$((8#$(stat -c '%a' "$tracked_file") & 8#022))" -eq 0 ]] || fail
done
for tracked_name in \
    scripts/run_private_corpus_processing.py \
    scripts/ingest_private_corpus_workspace.py \
    scripts/private_corpus_host_contract.py \
    scripts/private_corpus_processing_contract.py \
    scripts/receive_private_corpus.py \
    deploy/compose.yaml; do
    [[ "$(git -C "$release_dir" ls-files --error-unmatch -- "$tracked_name")" == "$tracked_name" ]] || fail
done

exec env -i PATH=/usr/sbin:/usr/bin SUDO_USER=cinegraph-corpus \
    timeout --signal=TERM --kill-after="${KILL_AFTER_SECONDS}s" "${TIMEOUT_SECONDS}s" \
    python3 -I -S -B "$processor"
