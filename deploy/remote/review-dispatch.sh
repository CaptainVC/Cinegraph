#!/usr/bin/env bash

# Forced-command boundary for the paid primary speaker-review identity.
set -euo pipefail
PATH=/usr/sbin:/usr/bin
export PATH

fail() {
    printf '%s\n' "speaker-review request rejected" >&2
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
[[ "$(id -un)" == "cinegraph-review" ]] || fail
[[ $# -eq 0 ]] || fail
check_root_path /usr directory 755
check_root_path /usr/bin directory 755
check_root_path /usr/sbin directory 755
check_root_path /usr/local directory 755
check_root_path /usr/local/libexec directory 755
check_root_path /usr/local/sbin directory 755
check_root_path /usr/local/libexec/cinegraph-review-dispatch file 755
check_root_path /usr/local/sbin/cinegraph-submit-private-speaker-review file 755
check_root_path /usr/local/sbin/cinegraph-observe-private-speaker-review file 755
check_root_path /usr/local/sbin/cinegraph-submit-next-private-speaker-review file 755
check_root_path /usr/local/sbin/cinegraph-observe-next-private-speaker-review file 755
check_root_path /usr/local/sbin/cinegraph-process-private-speaker-review-results file 755
check_root_path /usr/local/sbin/cinegraph-process-private-speaker-review-adjudication-results file 755
check_root_path /usr/local/sbin/cinegraph-submit-first-private-speaker-review-adjudication file 755
check_root_path /usr/local/sbin/cinegraph-observe-first-private-speaker-review-adjudication file 755
check_root_path /usr/local/sbin/cinegraph-submit-next-private-speaker-review-adjudication file 755
check_root_path /usr/local/sbin/cinegraph-observe-next-private-speaker-review-adjudication file 755
check_root_path /usr/local/sbin/cinegraph-submit-third-private-speaker-review-adjudication file 755
check_root_path /usr/local/sbin/cinegraph-observe-third-private-speaker-review-adjudication file 755
check_root_path /usr/local/sbin/cinegraph-submit-fourth-private-speaker-review-adjudication file 755
check_root_path /usr/local/sbin/cinegraph-observe-fourth-private-speaker-review-adjudication file 755
check_root_path /usr/local/sbin/cinegraph-submit-final-private-speaker-review file 755

case "${SSH_ORIGINAL_COMMAND-}" in
    speaker-review-submit-primary-v1)
        exec sudo -n /usr/local/sbin/cinegraph-submit-private-speaker-review
        ;;
    speaker-review-observe-primary-v1)
        exec sudo -n /usr/local/sbin/cinegraph-observe-private-speaker-review
        ;;
    speaker-review-submit-next-primary-v1)
        exec sudo -n /usr/local/sbin/cinegraph-submit-next-private-speaker-review
        ;;
    speaker-review-observe-next-primary-v1)
        exec sudo -n /usr/local/sbin/cinegraph-observe-next-private-speaker-review
        ;;
    speaker-review-process-primary-results-v1)
        exec sudo -n /usr/local/sbin/cinegraph-process-private-speaker-review-results
        ;;
    speaker-review-process-adjudication-results-v1)
        exec sudo -n /usr/local/sbin/cinegraph-process-private-speaker-review-adjudication-results
        ;;
    speaker-review-submit-first-adjudication-v1)
        exec sudo -n /usr/local/sbin/cinegraph-submit-first-private-speaker-review-adjudication
        ;;
    speaker-review-observe-first-adjudication-v1)
        exec sudo -n /usr/local/sbin/cinegraph-observe-first-private-speaker-review-adjudication
        ;;
    speaker-review-submit-next-adjudication-v1)
        exec sudo -n /usr/local/sbin/cinegraph-submit-next-private-speaker-review-adjudication
        ;;
    speaker-review-observe-next-adjudication-v1)
        exec sudo -n /usr/local/sbin/cinegraph-observe-next-private-speaker-review-adjudication
        ;;
    speaker-review-submit-third-adjudication-v1)
        exec sudo -n /usr/local/sbin/cinegraph-submit-third-private-speaker-review-adjudication
        ;;
    speaker-review-observe-third-adjudication-v1)
        exec sudo -n /usr/local/sbin/cinegraph-observe-third-private-speaker-review-adjudication
        ;;
    speaker-review-submit-fourth-adjudication-v1)
        exec sudo -n /usr/local/sbin/cinegraph-submit-fourth-private-speaker-review-adjudication
        ;;
    speaker-review-observe-fourth-adjudication-v1)
        exec sudo -n /usr/local/sbin/cinegraph-observe-fourth-private-speaker-review-adjudication
        ;;
    speaker-review-submit-final-review-v1)
        exec sudo -n /usr/local/sbin/cinegraph-submit-final-private-speaker-review
        ;;
    *)
        fail
        ;;
esac
