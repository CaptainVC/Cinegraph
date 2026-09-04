#!/usr/bin/env bash

# Forced-command boundary for the private-corpus transfer identity.
set -euo pipefail
PATH=/usr/sbin:/usr/bin
export PATH

fail() {
    printf '%s\n' "corpus transfer request rejected" >&2
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

[[ $EUID -ne 0 ]] || fail
[[ "$(id -un)" == "cinegraph-corpus" ]] || fail
[[ $# -eq 0 ]] || fail
check_root_path /usr directory 755
check_root_path /usr/bin directory 755
check_root_path /usr/sbin directory 755
check_root_path /usr/local directory 755
check_root_path /usr/local/libexec directory 755
check_root_path /usr/local/sbin directory 755
check_root_path /usr/local/libexec/cinegraph-corpus-dispatch file 755
check_root_path /usr/local/sbin/cinegraph-receive-private-corpus file 755
check_root_path /usr/local/sbin/cinegraph-process-private-corpus file 755

case "${SSH_ORIGINAL_COMMAND-}" in
    receive-v1)
        exec sudo -n /usr/local/sbin/cinegraph-receive-private-corpus
        ;;
    process-v1)
        exec sudo -n /usr/local/sbin/cinegraph-process-private-corpus
        ;;
    *)
        fail
        ;;
esac
