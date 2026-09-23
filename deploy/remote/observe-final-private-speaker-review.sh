#!/usr/bin/env bash

# Root-only entry point for one authorized final-review part-one observation.
set -euo pipefail
PATH=/usr/sbin:/usr/bin
export PATH
GIT_CONFIG_GLOBAL=/dev/null
GIT_CONFIG_SYSTEM=/dev/null
GIT_TERMINAL_PROMPT=0
export GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM GIT_TERMINAL_PROMPT

fail() { printf '%s\n' 'private final-review observation rejected' >&2; exit 1; }
check_root_path() {
    local path="$1" kind="$2" mode="$3"
    [[ ! -L "$path" ]] || fail
    if [[ "$kind" == directory ]]; then
        [[ -d "$path" ]] || fail
    else
        [[ -f "$path" && "$(stat -c '%h' "$path")" == 1 ]] || fail
    fi
    [[ "$(stat -c '%u:%g' "$path")" == 0:0 ]] || fail
    [[ "$(stat -c '%a' "$path")" == "$mode" ]] || fail
}

readonly DEPLOY_ROOT=/opt/cinegraph
readonly RELEASES_ROOT="$DEPLOY_ROOT/releases"
readonly CURRENT_LINK="$DEPLOY_ROOT/current"
readonly SHARED_ROOT="$DEPLOY_ROOT/shared"
readonly DEV_CORPUS_ROOT="$SHARED_ROOT/private-corpus/dev"
readonly SPEAKER_REVIEW_ROOT="$DEV_CORPUS_ROOT/speaker-review"
readonly AUTHORIZATION_ROOT="$SPEAKER_REVIEW_ROOT/authorization"
readonly SUBMISSION_RECEIPTS_ROOT="$SPEAKER_REVIEW_ROOT/final-review-submission-receipts"
readonly OBSERVATION_RECEIPTS_ROOT="$SPEAKER_REVIEW_ROOT/final-review-observation-receipts"
readonly RUNS_ROOT="$DEV_CORPUS_ROOT/review-runs"
readonly TRANSFER_LOCK="$DEV_CORPUS_ROOT/.transfer.lock"
readonly DEPLOYMENT_LOCK="$DEPLOY_ROOT/.deploy.lock"
readonly SPEAKER_REVIEW_LOCK="$DEV_CORPUS_ROOT/.speaker-review.lock"
readonly ENV_FILE=/etc/cinegraph/dev.env
readonly REPOSITORY_URL=https://github.com/CaptainVC/Cinegraph.git
readonly TIMEOUT_SECONDS=1860
readonly KILL_AFTER_SECONDS=10

[[ $EUID -eq 0 && $# -eq 0 && "${SUDO_USER-}" == cinegraph-review ]] || fail
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || fail
for command in docker env find flock git id python3 readlink stat timeout uname; do command -v "$command" >/dev/null 2>&1 || fail; done
docker compose version >/dev/null 2>&1 || fail
for spec in '/opt directory 755' '/opt/cinegraph directory 750' '/opt/cinegraph/releases directory 750' '/opt/cinegraph/shared directory 750' "$DEV_CORPUS_ROOT directory 700" "$SPEAKER_REVIEW_ROOT directory 700" "$AUTHORIZATION_ROOT directory 700" "$SUBMISSION_RECEIPTS_ROOT directory 700" "$OBSERVATION_RECEIPTS_ROOT directory 700" "$RUNS_ROOT directory 700" '/etc directory 755' '/etc/cinegraph directory 700' '/usr/local/sbin directory 755' '/etc/cinegraph/dev.env file 600'; do
    read -r path kind mode <<<"$spec"
    check_root_path "$path" "$kind" "$mode"
done
umask 077
[[ ! -L "$TRANSFER_LOCK" ]] || fail; exec 8>"$TRANSFER_LOCK"; [[ "$(stat -c '%u:%g:%a:%h' "$TRANSFER_LOCK")" == 0:0:600:1 ]] || fail; flock -n 8 || fail
[[ ! -L "$DEPLOYMENT_LOCK" ]] || fail; exec 9>"$DEPLOYMENT_LOCK"; [[ "$(stat -c '%u:%g:%a:%h' "$DEPLOYMENT_LOCK")" == 0:0:600:1 ]] || fail; flock -w 10 9 || fail
[[ ! -L "$SPEAKER_REVIEW_LOCK" ]] || fail; exec 7>"$SPEAKER_REVIEW_LOCK"; [[ "$(stat -c '%u:%g:%a:%h' "$SPEAKER_REVIEW_LOCK")" == 0:0:600:1 ]] || fail; flock -n 7 || fail
[[ -L "$CURRENT_LINK" ]] || fail
release_dir="$(readlink -f -- "$CURRENT_LINK")"
[[ "$release_dir" =~ ^/opt/cinegraph/releases/[0-9a-f]{40}$ ]] || fail
check_root_path "$release_dir" directory 750
[[ "$((8#$(stat -c '%a' "$release_dir") & 8#022))" -eq 0 ]] || fail
[[ -d "$release_dir/.git" && ! -L "$release_dir/.git" ]] || fail
[[ "$(git -C "$release_dir" remote)" == origin ]] || fail
[[ "$(git -C "$release_dir" remote get-url origin)" == "$REPOSITORY_URL" ]] || fail
[[ -z "$(git -C "$release_dir" status --porcelain=v1 --untracked-files=all)" ]] || fail
release_sha="$(git -C "$release_dir" rev-parse --verify HEAD)"
[[ "$release_dir" == "$RELEASES_ROOT/$release_sha" ]] || fail
[[ "$(git -C "$release_dir" rev-parse --verify refs/remotes/origin/main)" == "$release_sha" ]] || fail

directory_count=0
while IFS= read -r -d '' trusted_directory; do
    check_root_path "$trusted_directory" directory "$(stat -c '%a' "$trusted_directory")"
    [[ "$((8#$(stat -c '%a' "$trusted_directory") & 8#022))" -eq 0 ]] || fail
    directory_count=$((directory_count + 1))
done < <(find "$release_dir" -xdev -type d -print0)
[[ "$directory_count" -gt 0 ]] || fail
processor="$release_dir/scripts/run_private_speaker_review_final_review_observation.py"
for tracked_file in \
    "$processor" \
    "$release_dir/scripts/observe_final_private_speaker_review_workspace.py" \
    "$release_dir/scripts/private_speaker_review_final_review_observation_contract.py" \
    "$release_dir/scripts/private_speaker_review_final_review_observation_host_contract.py" \
    "$release_dir/scripts/run_private_speaker_review_final_review.py" \
    "$release_dir/scripts/private_speaker_review_final_review_submission_contract.py" \
    "$release_dir/scripts/private_speaker_review_final_review_host_contract.py" \
    "$release_dir/scripts/run_private_speaker_review_adjudication_result_processing.py" \
    "$release_dir/scripts/private_speaker_review_adjudication_result_processing_contract.py" \
    "$release_dir/deploy/compose.yaml" \
    "$release_dir/src/cinegraph/ingestion/speaker_review/workflow.py" \
    "$release_dir/src/cinegraph/adapters/workflow/langgraph/speaker_review_graph.py" \
    "$release_dir/src/cinegraph/common/speaker_review_cost_policy.py"; do
    check_root_path "$tracked_file" file "$(stat -c '%a' "$tracked_file")"
    [[ "$((8#$(stat -c '%a' "$tracked_file") & 8#022))" -eq 0 ]] || fail
    git -C "$release_dir" ls-files --error-unmatch -- "${tracked_file#"$release_dir/"}" >/dev/null || fail
done
tracked_count=0
while IFS= read -r -d '' tracked_name; do
    tracked_file="$release_dir/$tracked_name"
    check_root_path "$tracked_file" file "$(stat -c '%a' "$tracked_file")"
    [[ "$((8#$(stat -c '%a' "$tracked_file") & 8#022))" -eq 0 ]] || fail
    tracked_count=$((tracked_count + 1))
done < <(git -C "$release_dir" ls-files -z)
[[ "$tracked_count" -gt 0 ]] || fail
set +e
env -i PATH=/usr/sbin:/usr/bin SUDO_USER=cinegraph-review timeout --signal=TERM --kill-after="${KILL_AFTER_SECONDS}s" "${TIMEOUT_SECONDS}s" python3 -I -S -B "$processor"
status=$?
set -e
exit "$status"
