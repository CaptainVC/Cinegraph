#!/usr/bin/env bash
# Root-owned, no-argument wrapper for provider-free adjudication-result processing.
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
readonly SOURCE_ROOT="$SPEAKER_REVIEW_ROOT/source"
readonly RUNS_ROOT="$DEV_CORPUS_ROOT/review-runs"
readonly PROCESSING_RECEIPTS_ROOT="$SPEAKER_REVIEW_ROOT/adjudication-result-processing-receipts"
readonly TRANSFER_LOCK="$DEV_CORPUS_ROOT/.transfer.lock"
readonly DEPLOYMENT_LOCK="$DEPLOY_ROOT/.deploy.lock"
readonly SPEAKER_REVIEW_LOCK="$DEV_CORPUS_ROOT/.speaker-review.lock"
readonly ENV_FILE="/etc/cinegraph/dev.env"
readonly REPOSITORY_URL="https://github.com/CaptainVC/Cinegraph.git"
readonly TIMEOUT_SECONDS="1800"
readonly KILL_AFTER_SECONDS="10"
readonly CONTAINER_NAME="cinegraph-speaker-review-process-adjudication-results"
readonly COMPOSE_SERVICE="corpus-speaker-review-process-adjudication-results"

fail() { printf '%s\n' "private adjudication-result processing rejected" >&2; exit 1; }
check_root_path() {
    local path="$1" kind="$2" mode="$3"
    [[ ! -L "$path" ]] || fail
    if [[ "$kind" == "directory" ]]; then [[ -d "$path" ]] || fail; else [[ -f "$path" ]] || fail; fi
    [[ "$(stat -c '%u:%g' "$path")" == "0:0" ]] || fail
    [[ "$(stat -c '%a' "$path")" == "$mode" ]] || fail
}

cleanup_worker() {
    local expected_image=""
    local identity=""
    local -a lines=()
    local source_mount=""
    local source_mount_source=""
    local run_mount=""
    local run_mount_source=""
    local run_id=""
    local tmp_mount=""
    expected_image="$({
        timeout --signal=TERM --kill-after="${KILL_AFTER_SECONDS}s" "${KILL_AFTER_SECONDS}s" \
            docker compose --progress quiet \
            --env-file "$ENV_FILE" \
            --profile "$COMPOSE_SERVICE" \
            -f "$release_dir/deploy/compose.yaml" \
            config --images "$COMPOSE_SERVICE"
    } 2>/dev/null)" || return 0
    [[ -n "$expected_image" && "$expected_image" != *$'\n'* ]] || return 0
    identity="$({
        timeout --signal=TERM --kill-after="${KILL_AFTER_SECONDS}s" "${KILL_AFTER_SECONDS}s" \
            docker inspect --format \
            '{{.Name}}{{"\n"}}{{index .Config.Labels "com.docker.compose.service"}}{{"\n"}}{{index .Config.Labels "com.docker.compose.oneoff"}}{{"\n"}}{{index .Config.Labels "com.docker.compose.project.config_files"}}{{"\n"}}{{index .Config.Labels "com.docker.compose.project.working_dir"}}{{"\n"}}{{.Config.Image}}{{"\n"}}{{index .Config.Labels "com.docker.compose.project"}}{{"\n"}}{{.Config.User}}{{"\n"}}{{.Config.WorkingDir}}{{"\n"}}{{json .Config.Cmd}}{{"\n"}}{{.HostConfig.ReadonlyRootfs}}{{"\n"}}{{.HostConfig.Privileged}}{{"\n"}}{{json .HostConfig.CapDrop}}{{"\n"}}{{json .HostConfig.SecurityOpt}}{{"\n"}}{{.HostConfig.PidsLimit}}{{"\n"}}{{.HostConfig.Memory}}{{"\n"}}{{.HostConfig.NanoCpus}}{{"\n"}}{{json .Config.Env}}{{"\n"}}{{range $name, $configuration := .NetworkSettings.Networks}}{{printf "%s," $name}}{{end}}{{"\n"}}{{range .Mounts}}{{printf "%s|%s|%t\\n" .Source .Destination .RW}}{{end}}' \
            "$CONTAINER_NAME"
    } 2>/dev/null)" || return 0
    mapfile -t lines <<<"$identity"
    [[ "${#lines[@]}" -eq 22 ]] || return 0
    [[ "${lines[0]}" == "/$CONTAINER_NAME" ]] || return 0
    [[ "${lines[1]}" == "$COMPOSE_SERVICE" ]] || return 0
    [[ "${lines[2]}" == "True" ]] || return 0
    [[ "${lines[3]}" == "$release_dir/deploy/compose.yaml" ]] || return 0
    [[ "${lines[4]}" == "$release_dir" ]] || return 0
    [[ "${lines[5]}" == "$expected_image" ]] || return 0
    [[ "${lines[6]}" == "cinegraph-dev" ]] || return 0
    [[ "${lines[7]}" == "10002:10002" ]] || return 0
    [[ "${lines[8]}" == "/app" ]] || return 0
    [[ "${lines[9]}" == '["python","scripts/process_private_speaker_review_adjudication_results_workspace.py"]' ]] || return 0
    [[ "${lines[10]}" == "true" ]] || return 0
    [[ "${lines[11]}" == "false" ]] || return 0
    [[ "${lines[12]}" == '["ALL"]' ]] || return 0
    [[ "${lines[13]}" == '["no-new-privileges:true"]' ]] || return 0
    [[ "${lines[14]}" == "128" ]] || return 0
    [[ "${lines[15]}" =~ ^[0-9]+$ && "${lines[15]}" -ge 67108864 && "${lines[15]}" -le 4294967296 ]] || return 0
    [[ "${lines[16]}" =~ ^[0-9]+$ && "${lines[16]}" -ge 100000000 && "${lines[16]}" -le 4000000000 ]] || return 0
    [[ "${lines[17]}" != *'OPENAI_'* ]] || return 0
    [[ "${lines[17]}" != *'HTTP_PROXY='* && "${lines[17]}" != *'HTTPS_PROXY='* ]] || return 0
    [[ "${lines[17]}" != *'ALL_PROXY='* && "${lines[17]}" != *'API_KEY='* ]] || return 0
    [[ -z "${lines[18]}" ]] || return 0
    for mount in "${lines[@]:19}"; do
        case "$mount" in
            *"|/review-workspace|false")
                [[ -z "$source_mount" ]] || return 0
                source_mount_source="${mount%%|*}"
                [[ "$source_mount_source" =~ ^/opt/cinegraph/shared/private-corpus/dev/speaker-review/source/sha256-([0-9a-f]{64})$ ]] || return 0
                source_mount="$mount"
                ;;
            *"|/review-workspace/review-runs/"*"|true")
                [[ -z "$run_mount" ]] || return 0
                run_mount_source="${mount%%|*}"
                [[ "$run_mount_source" =~ ^/opt/cinegraph/shared/private-corpus/dev/review-runs/sha256-([0-9a-f]{64})/review-runs/(speaker-review-[0-9a-f]{16})$ ]] || return 0
                local run_digest="${BASH_REMATCH[1]}"
                run_id="${BASH_REMATCH[2]}"
                [[ "$mount" == "$run_mount_source|/review-workspace/review-runs/${run_id}|true" ]] || return 0
                run_mount="$mount"
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
    [[ -n "$source_mount" && -n "$run_mount" && -n "$tmp_mount" ]] || return 0
    [[ "$source_mount_source" =~ sha256-([0-9a-f]{64})$ ]] || return 0
    [[ "$run_digest" == "${BASH_REMATCH[1]}" ]] || return 0
    timeout --signal=TERM --kill-after="${KILL_AFTER_SECONDS}s" "${KILL_AFTER_SECONDS}s" \
        docker rm --force "$CONTAINER_NAME" >/dev/null 2>&1 || true
}

[[ $EUID -eq 0 ]] || fail
[[ $# -eq 0 ]] || fail
[[ "${SUDO_USER-}" == "cinegraph-review" ]] || fail
[[ "$(uname -s)" == "Linux" ]] || fail
[[ "$(uname -m)" == "x86_64" ]] || fail
for command in docker env find flock git id python3 readlink stat timeout uname; do command -v "$command" >/dev/null 2>&1 || fail; done
docker compose version >/dev/null 2>&1 || fail
for pair in "/opt directory 755" "/etc directory 755" "/etc/cinegraph directory 700" "/usr directory 755" "/usr/bin directory 755" "/usr/sbin directory 755" "/usr/local directory 755" "/usr/local/sbin directory 755" "/usr/local/libexec directory 755" \
    "$DEPLOY_ROOT directory 750" "$RELEASES_ROOT directory 750" "$SHARED_ROOT directory 750" "$CORPUS_ROOT directory 700" "$DEV_CORPUS_ROOT directory 700" "$SPEAKER_REVIEW_ROOT directory 700" "$SOURCE_ROOT directory 700" "$PROCESSING_RECEIPTS_ROOT directory 700" "$ENV_FILE file 600" \
    "/usr/local/sbin/cinegraph-process-private-speaker-review-adjudication-results file 755"; do
    read -r path kind mode <<<"$pair"; check_root_path "$path" "$kind" "$mode"
done
check_root_path "$RUNS_ROOT" directory 700

umask 077
[[ ! -L "$TRANSFER_LOCK" ]] || fail; exec 8>"$TRANSFER_LOCK"; [[ "$(stat -c '%u:%g:%a:%h' "$TRANSFER_LOCK")" == "0:0:600:1" ]] || fail; flock -n 8 || fail
[[ ! -L "$DEPLOYMENT_LOCK" ]] || fail; exec 9>"$DEPLOYMENT_LOCK"; [[ "$(stat -c '%u:%g:%a:%h' "$DEPLOYMENT_LOCK")" == "0:0:600:1" ]] || fail; flock -w 10 9 || fail
[[ ! -L "$SPEAKER_REVIEW_LOCK" ]] || fail; exec 7>"$SPEAKER_REVIEW_LOCK"; [[ "$(stat -c '%u:%g:%a:%h' "$SPEAKER_REVIEW_LOCK")" == "0:0:600:1" ]] || fail; flock -n 7 || fail

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
[[ "$release_sha" =~ ^[0-9a-f]{40}$ && "$release_dir" == "$RELEASES_ROOT/$release_sha" ]] || fail
[[ "$(git -C "$release_dir" rev-parse --verify refs/remotes/origin/main)" == "$release_sha" ]] || fail

# Validate every directory that can influence an imported module before
# launching Python; the deployment lock prevents legitimate replacement.
directory_count=0
while IFS= read -r -d '' trusted_directory; do
    check_root_path "$trusted_directory" directory "$(stat -c '%a' "$trusted_directory")"
    [[ "$((8#$(stat -c '%a' "$trusted_directory") & 8#022))" -eq 0 ]] || fail
    directory_count=$((directory_count + 1))
done < <(find "$release_dir" -xdev -type d -print0)
[[ "$directory_count" -gt 0 ]] || fail

processor="$release_dir/scripts/run_private_speaker_review_adjudication_result_processing.py"
worker="$release_dir/scripts/process_private_speaker_review_adjudication_results_workspace.py"
processing_contract="$release_dir/scripts/private_speaker_review_adjudication_result_processing_contract.py"
processing_host_contract="$release_dir/scripts/private_speaker_review_adjudication_result_processing_host_contract.py"
compose="$release_dir/deploy/compose.yaml"
trusted_files=(
    "$processor"
    "$worker"
    "$processing_contract"
    "$processing_host_contract"
    "$release_dir/scripts/__init__.py"
    "$release_dir/scripts/process_private_speaker_review_results_workspace.py"
    "$release_dir/scripts/run_private_speaker_review.py"
    "$release_dir/scripts/private_speaker_review_contract.py"
    "$release_dir/scripts/private_corpus_processing_contract.py"
    "$release_dir/scripts/receive_private_corpus.py"
    "$release_dir/scripts/run_private_corpus_processing.py"
    "$release_dir/scripts/run_private_speaker_review_primary_result_processing.py"
    "$release_dir/scripts/private_speaker_review_primary_result_processing_contract.py"
    "$release_dir/scripts/private_speaker_review_primary_result_processing_host_contract.py"
    "$release_dir/scripts/run_private_speaker_review_first_adjudication_observation.py"
    "$release_dir/scripts/private_speaker_review_first_adjudication_observation_contract.py"
    "$release_dir/scripts/private_speaker_review_first_adjudication_observation_host_contract.py"
    "$release_dir/scripts/run_private_speaker_review_first_adjudication.py"
    "$release_dir/scripts/private_speaker_review_first_adjudication_submission_contract.py"
    "$release_dir/scripts/private_speaker_review_first_adjudication_host_contract.py"
    "$release_dir/scripts/run_private_speaker_review_third_adjudication_observation.py"
    "$release_dir/scripts/private_speaker_review_third_adjudication_observation_contract.py"
    "$release_dir/scripts/private_speaker_review_third_adjudication_observation_host_contract.py"
    "$release_dir/scripts/run_private_speaker_review_third_adjudication.py"
    "$release_dir/scripts/private_speaker_review_third_adjudication_submission_contract.py"
    "$release_dir/scripts/private_speaker_review_third_adjudication_host_contract.py"
    "$release_dir/scripts/run_private_speaker_review_fourth_adjudication_observation.py"
    "$release_dir/scripts/private_speaker_review_fourth_adjudication_observation_contract.py"
    "$release_dir/scripts/private_speaker_review_fourth_adjudication_observation_host_contract.py"
    "$release_dir/scripts/run_private_speaker_review_fourth_adjudication.py"
    "$release_dir/scripts/private_speaker_review_fourth_adjudication_submission_contract.py"
    "$release_dir/scripts/private_speaker_review_fourth_adjudication_host_contract.py"
    "$release_dir/scripts/run_private_speaker_review_next_adjudication_observation.py"
    "$release_dir/scripts/observe_next_private_speaker_review_adjudication_workspace.py"
    "$release_dir/scripts/private_speaker_review_next_adjudication_observation_contract.py"
    "$release_dir/scripts/private_speaker_review_next_adjudication_observation_host_contract.py"
    "$release_dir/scripts/run_private_speaker_review_next_adjudication.py"
    "$release_dir/scripts/private_speaker_review_next_adjudication_submission_contract.py"
    "$release_dir/scripts/private_speaker_review_next_adjudication_host_contract.py"
    "$release_dir/scripts/run_private_speaker_review_next_primary.py"
    "$release_dir/scripts/private_speaker_review_next_primary_submission_contract.py"
    "$release_dir/scripts/private_speaker_review_next_primary_host_contract.py"
    "$release_dir/scripts/run_private_speaker_review_next_primary_observation.py"
    "$release_dir/scripts/private_speaker_review_next_primary_observation_contract.py"
    "$release_dir/scripts/private_speaker_review_next_primary_observation_host_contract.py"
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

# Cover every transitive host-side Python import and tracked deployment asset,
# rather than relying solely on the explicit critical-file list above.
tracked_count=0
while IFS= read -r -d '' tracked_name; do
    tracked_file="$release_dir/$tracked_name"
    check_root_path "$tracked_file" file "$(stat -c '%a' "$tracked_file")"
    [[ "$((8#$(stat -c '%a' "$tracked_file") & 8#022))" -eq 0 ]] || fail
    tracked_count=$((tracked_count + 1))
done < <(git -C "$release_dir" ls-files -z)
[[ "$tracked_count" -gt 0 ]] || fail
for tracked_name in \
    scripts/run_private_speaker_review_adjudication_result_processing.py \
    scripts/process_private_speaker_review_adjudication_results_workspace.py \
    scripts/private_speaker_review_adjudication_result_processing_contract.py \
    scripts/private_speaker_review_adjudication_result_processing_host_contract.py \
    scripts/__init__.py \
    scripts/process_private_speaker_review_results_workspace.py \
    scripts/run_private_speaker_review.py \
    scripts/private_speaker_review_contract.py \
    scripts/private_corpus_processing_contract.py \
    scripts/receive_private_corpus.py \
    scripts/run_private_corpus_processing.py \
    scripts/run_private_speaker_review_primary_result_processing.py \
    scripts/private_speaker_review_primary_result_processing_contract.py \
    scripts/private_speaker_review_primary_result_processing_host_contract.py \
    scripts/run_private_speaker_review_first_adjudication_observation.py \
    scripts/private_speaker_review_first_adjudication_observation_contract.py \
    scripts/private_speaker_review_first_adjudication_observation_host_contract.py \
    scripts/run_private_speaker_review_first_adjudication.py \
    scripts/private_speaker_review_first_adjudication_submission_contract.py \
    scripts/private_speaker_review_first_adjudication_host_contract.py \
    scripts/run_private_speaker_review_third_adjudication_observation.py \
    scripts/private_speaker_review_third_adjudication_observation_contract.py \
    scripts/private_speaker_review_third_adjudication_observation_host_contract.py \
    scripts/run_private_speaker_review_third_adjudication.py \
    scripts/private_speaker_review_third_adjudication_submission_contract.py \
    scripts/private_speaker_review_third_adjudication_host_contract.py \
    scripts/run_private_speaker_review_fourth_adjudication_observation.py \
    scripts/private_speaker_review_fourth_adjudication_observation_contract.py \
    scripts/private_speaker_review_fourth_adjudication_observation_host_contract.py \
    scripts/run_private_speaker_review_fourth_adjudication.py \
    scripts/private_speaker_review_fourth_adjudication_submission_contract.py \
    scripts/private_speaker_review_fourth_adjudication_host_contract.py \
    scripts/run_private_speaker_review_next_adjudication_observation.py \
    scripts/observe_next_private_speaker_review_adjudication_workspace.py \
    scripts/private_speaker_review_next_adjudication_observation_contract.py \
    scripts/private_speaker_review_next_adjudication_observation_host_contract.py \
    scripts/run_private_speaker_review_next_adjudication.py \
    scripts/private_speaker_review_next_adjudication_submission_contract.py \
    scripts/private_speaker_review_next_adjudication_host_contract.py \
    scripts/run_private_speaker_review_next_primary.py \
    scripts/private_speaker_review_next_primary_submission_contract.py \
    scripts/private_speaker_review_next_primary_host_contract.py \
    scripts/run_private_speaker_review_next_primary_observation.py \
    scripts/private_speaker_review_next_primary_observation_contract.py \
    scripts/private_speaker_review_next_primary_observation_host_contract.py \
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
env -i PATH=/usr/sbin:/usr/bin SUDO_USER=cinegraph-review timeout --signal=TERM --kill-after="${KILL_AFTER_SECONDS}s" "${TIMEOUT_SECONDS}s" python3 -I -S -B "$processor"
status=$?
set -e
if [[ "$status" -ne 0 ]]; then
    cleanup_worker
fi
exit "$status"
