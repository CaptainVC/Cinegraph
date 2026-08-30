#!/usr/bin/env bash

# Forced-command boundary for the unprivileged Cinegraph deployment SSH account.
set -euo pipefail
PATH=/usr/sbin:/usr/bin
export PATH

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

check_root_path() {
    local path="$1"
    local kind="$2"
    local mode="$3"
    [[ ! -L "$path" ]] || fail "deployment boundary path must not be a symlink"
    if [[ "$kind" == "directory" ]]; then
        [[ -d "$path" ]] || fail "deployment boundary directory is missing"
    else
        [[ -f "$path" ]] || fail "deployment boundary file is missing"
    fi
    [[ "$(stat -c '%u:%g' "$path")" == "0:0" ]] || fail "deployment boundary must be root-owned"
    [[ "$(stat -c '%a' "$path")" == "$mode" ]] || fail "deployment boundary mode is invalid"
}

[[ $EUID -ne 0 ]] || fail "deployment dispatcher must not run as root"
[[ "$(id -un)" == "cinegraph-deploy" ]] || fail "deployment dispatcher requires the dedicated account"
[[ $# -eq 0 ]] || fail "deployment dispatcher accepts no command-line arguments"
check_root_path /usr directory 755
check_root_path /usr/bin directory 755
check_root_path /usr/sbin directory 755
check_root_path /usr/local directory 755
check_root_path /usr/local/libexec directory 755
check_root_path /usr/local/sbin directory 755
check_root_path /usr/local/libexec/cinegraph-deploy-dispatch file 755
check_root_path /usr/local/sbin/cinegraph-deploy-dev file 755

original_command="${SSH_ORIGINAL_COMMAND-}"
if [[ ! "$original_command" =~ ^deploy\ ([0-9a-f]{40})\ (sha256:[0-9a-f]{64})$ ]]; then
    fail "SSH command is not an authorized deployment request"
fi
release_sha="${BASH_REMATCH[1]}"
image_digest="${BASH_REMATCH[2]}"

printf '%s\n%s\n' "$release_sha" "$image_digest" | \
    sudo -n /usr/local/sbin/cinegraph-deploy-dev
