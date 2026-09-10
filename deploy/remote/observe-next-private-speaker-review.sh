#!/usr/bin/env bash

# Root-owned, no-argument wrapper for one bounded part-two observation.
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
readonly SPEAKER_REVIEW_ROOT="$DEV_CORPUS_ROOT/speaker-review"
readonly AUTHORIZATION_ROOT="$SPEAKER_REVIEW_ROOT/authorization"
readonly SUBMISSION_RECEIPTS_ROOT="$SPEAKER_REVIEW_ROOT/submission-receipts"
readonly OBSERVATION_RECEIPTS_ROOT="$SPEAKER_REVIEW_ROOT/observation-receipts"
readonly NEXT_RECEIPTS_ROOT="$SPEAKER_REVIEW_ROOT/next-primary-receipts"
readonly RUNS_ROOT="$DEV_CORPUS_ROOT/review-runs"
readonly TRANSFER_LOCK="$DEV_CORPUS_ROOT/.transfer.lock"
readonly DEPLOYMENT_LOCK="$DEPLOY_ROOT/.deploy.lock"
readonly SPEAKER_REVIEW_LOCK="$DEV_CORPUS_ROOT/.speaker-review.lock"
readonly ENV_FILE="/etc/cinegraph/dev.env"
readonly REPOSITORY_URL="https://github.com/CaptainVC/Cinegraph.git"
readonly TIMEOUT_SECONDS="1800"
readonly KILL_AFTER_SECONDS="10"
readonly CONTAINER_NAME="cinegraph-speaker-review-observe-primary"
readonly COMPOSE_SERVICE="corpus-speaker-review-observe-primary"

fail() {
    printf '%s\n' "private next-primary observation rejected" >&2
    exit 1
}

cleanup_worker() {
    local expected_image=""
    local identity=""
    local -a lines=()
    local review_mount=""
    local review_mount_source=""
    local secret_mount=""
    local tmp_mount=""
    expected_image="$(
        timeout --signal=TERM --kill-after="${KILL_AFTER_SECONDS}s" "${KILL_AFTER_SECONDS}s" \
            docker compose --progress quiet \
            --env-file "$ENV_FILE" \
            --profile corpus-speaker-review-observe-primary \
            -f "$release_dir/deploy/compose.yaml" \
            config --images "$COMPOSE_SERVICE" 2>/dev/null
    )" || return 0
    [[ -n "$expected_image" && "$expected_image" != *$'\n'* ]] || return 0
    identity="$(
        timeout --signal=TERM --kill-after="${KILL_AFTER_SECONDS}s" "${KILL_AFTER_SECONDS}s" \
            docker inspect --format \
            '{{.Name}}{{"\n"}}{{index .Config.Labels "com.docker.compose.service"}}{{"\n"}}{{index .Config.Labels "com.docker.compose.oneoff"}}{{"\n"}}{{index .Config.Labels "com.docker.compose.project.config_files"}}{{"\n"}}{{index .Config.Labels "com.docker.compose.project.working_dir"}}{{"\n"}}{{.Config.Image}}{{"\n"}}{{index .Config.Labels "com.docker.compose.project"}}{{"\n"}}{{.Config.User}}{{"\n"}}{{.Config.WorkingDir}}{{"\n"}}{{json .Config.Cmd}}{{"\n"}}{{.HostConfig.ReadonlyRootfs}}{{"\n"}}{{.HostConfig.Privileged}}{{"\n"}}{{json .HostConfig.CapDrop}}{{"\n"}}{{json .HostConfig.SecurityOpt}}{{"\n"}}{{.HostConfig.PidsLimit}}{{"\n"}}{{json .Config.Env}}{{"\n"}}{{range $name, $configuration := .NetworkSettings.Networks}}{{printf "%s," $name}}{{end}}{{"\n"}}{{range .Mounts}}{{printf "%s|%s|%t\\n" .Source .Destination .RW}}{{end}}' \
            "$CONTAINER_NAME" 2>/dev/null
    )" || return 0
    mapfile -t lines <<<"$identity"
    [[ "${#lines[@]}" -eq 20 ]] || return 0
    [[ "${lines[0]}" == "/$CONTAINER_NAME" ]] || return 0
    [[ "${lines[1]}" == "$COMPOSE_SERVICE" ]] || return 0
    [[ "${lines[2]}" == "True" ]] || return 0
    [[ "${lines[3]}" == "$release_dir/deploy/compose.yaml" ]] || return 0
    [[ "${lines[4]}" == "$release_dir" ]] || return 0
    [[ "${lines[5]}" == "$expected_image" ]] || return 0
    [[ "${lines[6]}" == "cinegraph-dev" ]] || return 0
    [[ "${lines[7]}" == "10002:10002" ]] || return 0
    [[ "${lines[8]}" == "/app" ]] || return 0
    [[ "${lines[9]}" == '["python","scripts/observe_private_speaker_review_workspace.py"]' ]] || return 0
    [[ "${lines[10]}" == "true" ]] || return 0
    [[ "${lines[11]}" == "false" ]] || return 0
    [[ "${lines[12]}" == '["ALL"]' ]] || return 0
    [[ "${lines[13]}" == '["no-new-privileges:true"]' ]] || return 0
    [[ "${lines[14]}" == "128" ]] || return 0
    [[ "${lines[15]}" != *'OPENAI_API_KEY='* ]] || return 0
    [[ "${lines[16]}" == "cinegraph-dev_egress," ]] || return 0
    for mount in "${lines[@]:17}"; do
        case "$mount" in
            *"|/review-workspace/review-runs|true")
                [[ -z "$review_mount" ]] || return 0
                review_mount_source="${mount%%|*}"
                [[ "$review_mount_source" =~ ^/opt/cinegraph/shared/private-corpus/dev/review-runs/sha256-[0-9a-f]{64}/review-runs$ ]] || return 0
                review_mount="$mount"
                ;;
            *"|/run/secrets/openai_api_key|false")
                [[ -z "$secret_mount" && "$mount" != "|/run/secrets/openai_api_key|false" ]] || return 0
                secret_mount="$mount"
                ;;
            "|/tmp|true")
                [[ -z "$tmp_mount" ]] || return 0
                tmp_mount="$mount"
                ;;
            *)
                return 0
                ;;
        esac
    done
    [[ -n "$review_mount" && -n "$secret_mount" && -n "$tmp_mount" ]] || return 0
    timeout --signal=TERM --kill-after="${KILL_AFTER_SECONDS}s" "${KILL_AFTER_SECONDS}s" \
        docker rm --force "$CONTAINER_NAME" >/dev/null 2>&1 || true
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
[[ "${SUDO_USER-}" == "cinegraph-review" ]] || fail
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
check_root_path "$SPEAKER_REVIEW_ROOT" directory 700
check_root_path "$AUTHORIZATION_ROOT" directory 700
check_root_path "$SUBMISSION_RECEIPTS_ROOT" directory 700
check_root_path "$OBSERVATION_RECEIPTS_ROOT" directory 700
check_root_path "$RUNS_ROOT" directory 700
check_root_path "$ENV_FILE" file 600
check_root_path /usr/local/sbin/cinegraph-observe-next-private-speaker-review file 755
check_root_path "$NEXT_RECEIPTS_ROOT" directory 700

umask 077
# Every private worker acquires locks in transfer -> deployment -> review order.
[[ ! -L "$TRANSFER_LOCK" ]] || fail
exec 8>"$TRANSFER_LOCK"
[[ -f "$TRANSFER_LOCK" && "$(stat -c '%u:%g:%a:%h' "$TRANSFER_LOCK")" == "0:0:600:1" ]] || fail
flock -n 8 || fail
[[ ! -L "$DEPLOYMENT_LOCK" ]] || fail
exec 9>"$DEPLOYMENT_LOCK"
[[ -f "$DEPLOYMENT_LOCK" && "$(stat -c '%u:%g:%a:%h' "$DEPLOYMENT_LOCK")" == "0:0:600:1" ]] || fail
flock -w 10 9 || fail
[[ ! -L "$SPEAKER_REVIEW_LOCK" ]] || fail
exec 7>"$SPEAKER_REVIEW_LOCK"
[[ -f "$SPEAKER_REVIEW_LOCK" && "$(stat -c '%u:%g:%a:%h' "$SPEAKER_REVIEW_LOCK")" == "0:0:600:1" ]] || fail
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

processor="$release_dir/scripts/run_private_speaker_review_next_primary_observation.py"
worker="$release_dir/scripts/observe_private_speaker_review_workspace.py"
next_contract="$release_dir/scripts/private_speaker_review_next_primary_observation_contract.py"
next_host_contract="$release_dir/scripts/private_speaker_review_next_primary_observation_host_contract.py"
compose="$release_dir/deploy/compose.yaml"
trusted_files=(
    "$processor"
    "$worker"
    "$next_contract"
    "$next_host_contract"
    "$release_dir/scripts/run_private_speaker_review_next_primary.py"
    "$release_dir/scripts/private_speaker_review_next_primary_submission_contract.py"
    "$release_dir/scripts/private_speaker_review_next_primary_host_contract.py"
    "$release_dir/scripts/run_private_speaker_review_observation.py"
    "$release_dir/scripts/private_speaker_review_observation_contract.py"
    "$release_dir/scripts/private_speaker_review_observation_host_contract.py"
    "$release_dir/scripts/run_private_speaker_review_submission.py"
    "$release_dir/scripts/private_speaker_review_submission_contract.py"
    "$release_dir/scripts/private_speaker_review_submission_host_contract.py"
    "$release_dir/scripts/private_corpus_host_contract.py"
    "$release_dir/scripts/dev_host_contract.py"
    "$compose"
)
for tracked_file in "${trusted_files[@]}"; do
    check_root_path "$tracked_file" file "$(stat -c '%a' "$tracked_file")"
    [[ "$((8#$(stat -c '%a' "$tracked_file") & 8#022))" -eq 0 ]] || fail
done
for tracked_name in \
    scripts/run_private_speaker_review_next_primary_observation.py \
    scripts/observe_private_speaker_review_workspace.py \
    scripts/private_speaker_review_next_primary_observation_contract.py \
    scripts/private_speaker_review_next_primary_observation_host_contract.py \
    scripts/run_private_speaker_review_next_primary.py \
    scripts/private_speaker_review_next_primary_submission_contract.py \
    scripts/private_speaker_review_next_primary_host_contract.py \
    scripts/run_private_speaker_review_observation.py \
    scripts/private_speaker_review_observation_contract.py \
    scripts/private_speaker_review_observation_host_contract.py \
    scripts/run_private_speaker_review_submission.py \
    scripts/private_speaker_review_submission_contract.py \
    scripts/private_speaker_review_submission_host_contract.py \
    scripts/private_corpus_host_contract.py \
    scripts/dev_host_contract.py \
    deploy/compose.yaml; do
    [[ "$(git -C "$release_dir" ls-files --error-unmatch -- "$tracked_name")" == "$tracked_name" ]] || fail
done

set +e
env -i PATH=/usr/sbin:/usr/bin SUDO_USER=cinegraph-review \
    timeout --signal=TERM --kill-after="${KILL_AFTER_SECONDS}s" "${TIMEOUT_SECONDS}s" \
    python3 -I -S -B "$processor"
status=$?
set -e
if [[ "$status" -ne 0 ]]; then
    cleanup_worker
fi
exit "$status"
