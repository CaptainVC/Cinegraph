#!/usr/bin/env bash
# Root-owned forced-command boundary for exactly final-review part two.
set -euo pipefail
PATH=/usr/sbin:/usr/bin
export PATH
fail() { printf '%s\n' 'private next final-review submission rejected' >&2; exit 1; }
[[ $EUID -eq 0 && $# -eq 0 && "${SUDO_USER-}" == cinegraph-review ]] || fail
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || fail
for command in docker env flock git python3 readlink stat timeout uname; do command -v "$command" >/dev/null 2>&1 || fail; done
docker compose version >/dev/null 2>&1 || fail
readonly ROOT=/opt/cinegraph
readonly RELEASES="$ROOT/releases"
readonly CURRENT="$ROOT/current"
readonly SHARED="$ROOT/shared"
readonly CORPUS="$SHARED/private-corpus/dev"
readonly REVIEW="$CORPUS/speaker-review"
readonly AUTH="$REVIEW/authorization"
readonly P78="$REVIEW/adjudication-result-processing-receipts"
readonly P79="$REVIEW/final-review-submission-receipts"
readonly P80="$REVIEW/final-review-observation-receipts"
readonly NEXT="$REVIEW/next-final-review-submission-receipts"
readonly RUNS="$CORPUS/review-runs"
readonly ENV_FILE=/etc/cinegraph/dev.env
readonly REPOSITORY_URL=https://github.com/CaptainVC/Cinegraph.git
readonly HELPER=/usr/local/sbin/cinegraph-submit-next-final-private-speaker-review
check() { local path="$1" kind="$2" mode="$3"; [[ ! -L "$path" ]] || fail; [[ "$kind" == directory && -d "$path" || "$kind" == file && -f "$path" ]] || fail; [[ "$(stat -c '%u:%g:%a' "$path")" == "0:0:$mode" ]] || fail; }
check /opt directory 755; check "$ROOT" directory 750; check "$RELEASES" directory 750; check "$SHARED" directory 750
check "$CORPUS" directory 700; check "$REVIEW" directory 700; check "$AUTH" directory 700; check "$P78" directory 700; check "$P79" directory 700; check "$P80" directory 700; check "$NEXT" directory 700; check "$RUNS" directory 700; check "$ENV_FILE" file 600; check "$HELPER" file 755
exec 8>"$CORPUS/.transfer.lock"; flock -n 8 || fail; exec 9>"$ROOT/.deploy.lock"; flock -w 10 9 || fail; exec 7>"$CORPUS/.speaker-review.lock"; flock -n 7 || fail
[[ -L "$CURRENT" ]] || fail
release_dir="$(readlink -f -- "$CURRENT")"; [[ "$release_dir" =~ ^/opt/cinegraph/releases/[0-9a-f]{40}$ ]] || fail; check "$release_dir" directory 750
[[ -d "$release_dir/.git" && "$(git -C "$release_dir" remote get-url origin)" == "$REPOSITORY_URL" && -z "$(git -C "$release_dir" status --porcelain=v1 --untracked-files=all)" ]] || fail
release_sha="$(git -C "$release_dir" rev-parse --verify HEAD)"; [[ "$release_dir" == "$RELEASES/$release_sha" && "$(git -C "$release_dir" rev-parse --verify refs/remotes/origin/main)" == "$release_sha" ]] || fail
for tracked in \
    scripts/run_private_speaker_review_next_final_review.py \
    scripts/submit_next_final_private_speaker_review_workspace.py \
    scripts/private_speaker_review_next_final_review_submission_contract.py \
    scripts/private_speaker_review_next_final_review_host_contract.py \
    scripts/private_speaker_review_final_review_submission_contract.py \
    scripts/private_speaker_review_final_review_observation_contract.py \
    scripts/private_speaker_review_final_review_host_contract.py \
    scripts/private_speaker_review_final_review_observation_host_contract.py \
    scripts/private_speaker_review_adjudication_result_processing_contract.py \
    deploy/compose.yaml \
    src/cinegraph/ingestion/speaker_review/workflow.py \
    src/cinegraph/adapters/workflow/langgraph/speaker_review_graph.py \
    src/cinegraph/common/speaker_review_cost_policy.py; do
    path="$release_dir/$tracked"
    check "$path" file 644
    git -C "$release_dir" ls-files --error-unmatch -- "$tracked" >/dev/null || fail
done
set +e
env -i PATH=/usr/sbin:/usr/bin SUDO_USER=cinegraph-review timeout --signal=TERM --kill-after=10s 1800s python3 -I -S -B "$release_dir/scripts/run_private_speaker_review_next_final_review.py"
status=$?
set -e
exit "$status"
