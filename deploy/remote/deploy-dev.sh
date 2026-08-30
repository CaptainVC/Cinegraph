#!/usr/bin/env bash

# Promote one already-attested Cinegraph image to the Dev runtime. This script is
# sent over the pinned SSH connection and deliberately contains no credentials.
set -euo pipefail

readonly DEPLOY_ROOT="/opt/cinegraph"
readonly RELEASES_ROOT="$DEPLOY_ROOT/releases"
readonly CURRENT_LINK="$DEPLOY_ROOT/current"
readonly ENV_FILE="/etc/cinegraph/dev.env"
readonly REPOSITORY_URL="https://github.com/CaptainVC/Cinegraph.git"
readonly IMAGE_NAME="ghcr.io/captainvc/cinegraph"
readonly DEV_COMPOSE_PORT="18000"

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

[[ $# -eq 2 ]] || fail "deployment requires a release SHA and image digest"
release_sha="$1"
image_digest="$2"
[[ "$release_sha" =~ ^[0-9a-f]{40}$ ]] || fail "release SHA is invalid"
[[ "$image_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "image digest is invalid"

[[ "$(uname -s)" == "Linux" ]] || fail "Dev deployment requires Linux"
[[ "$(uname -m)" == "x86_64" ]] || fail "Dev deployment requires linux/amd64"
command -v git >/dev/null 2>&1 || fail "git is unavailable on the Dev host"
command -v docker >/dev/null 2>&1 || fail "docker is unavailable on the Dev host"
command -v curl >/dev/null 2>&1 || fail "curl is unavailable on the Dev host"
command -v flock >/dev/null 2>&1 || fail "flock is unavailable on the Dev host"
command -v awk >/dev/null 2>&1 || fail "awk is unavailable on the Dev host"
command -v find >/dev/null 2>&1 || fail "find is unavailable on the Dev host"
command -v mktemp >/dev/null 2>&1 || fail "mktemp is unavailable on the Dev host"
command -v python3 >/dev/null 2>&1 || fail "python3 is unavailable on the Dev host"
command -v sed >/dev/null 2>&1 || fail "sed is unavailable on the Dev host"
command -v stat >/dev/null 2>&1 || fail "stat is unavailable on the Dev host"
docker compose version >/dev/null 2>&1 || fail "docker compose is unavailable on the Dev host"
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || fail "Dev environment file must be a regular file"
[[ "$(stat -c '%a' "$ENV_FILE")" == "600" ]] || fail "Dev environment file must have mode 0600"

umask 077
mkdir -p "$DEPLOY_ROOT" "$RELEASES_ROOT"
exec 9>"$DEPLOY_ROOT/.deploy.lock"
flock -n 9 || fail "another Dev deployment is already running"
candidate_env="$(mktemp /etc/cinegraph/dev.env.candidate.XXXXXX)"
candidate_cleanup() { rm -f "$candidate_env"; }
trap candidate_cleanup EXIT

release_dir="$RELEASES_ROOT/$release_sha"
previous_env="$ENV_FILE.previous"
[[ ! -e "$previous_env" && ! -L "$previous_env" ]] || fail "previous Dev environment backup exists; recover before retry"

staging_parent=""
cleanup_staging() {
    if [[ -n "$staging_parent" && -d "$staging_parent" ]]; then
        find "$staging_parent" -depth -type f -delete
        find "$staging_parent" -depth -type l -delete
        find "$staging_parent" -depth -type d -empty -delete
    fi
}
trap 'candidate_cleanup; cleanup_staging' EXIT

verify_release_checkout() {
    local checkout_dir="$1"
    [[ -d "$checkout_dir/.git" ]] || fail "release checkout has no Git metadata"
    [[ "$(git -C "$checkout_dir" remote)" == "origin" ]] || fail "release checkout has unexpected Git remotes"
    [[ "$(git -C "$checkout_dir" remote get-url origin)" == "$REPOSITORY_URL" ]] || fail "release checkout origin is not approved"
    [[ -z "$(git -C "$checkout_dir" status --porcelain=v1 --untracked-files=all)" ]] || fail "release checkout is dirty"
    [[ "$(git -C "$checkout_dir" rev-parse --verify HEAD)" == "$release_sha" ]] || fail "release checkout is not the requested SHA"
}

if [[ -e "$release_dir" ]]; then
    [[ ! -L "$release_dir" ]] || fail "release checkout must not be a symlink"
    verify_release_checkout "$release_dir"
else
    staging_parent="$(mktemp -d "$RELEASES_ROOT/.staging.XXXXXX")"
    staging_dir="$staging_parent/release"
    git clone --quiet --no-checkout "$REPOSITORY_URL" "$staging_dir"
    git -C "$staging_dir" fetch --quiet --depth=1 origin "$release_sha"
    git -C "$staging_dir" checkout --quiet --detach "$release_sha"
    verify_release_checkout "$staging_dir"
    mv -T "$staging_dir" "$release_dir"
    rmdir "$staging_parent"
    staging_parent=""
fi

awk -v image_name="$IMAGE_NAME" -v digest="$image_digest" -v release_sha="$release_sha" -v env_file="$candidate_env" '
    BEGIN { image_found = 0; digest_found = 0; sha_found = 0 }
    /^CINEGRAPH_IMAGE=/ { if ($0 != "CINEGRAPH_IMAGE=" image_name) exit 20; image_found = 1 }
    /^CINEGRAPH_ENV_FILE=/ { print "CINEGRAPH_ENV_FILE=" env_file; next }
    /^CINEGRAPH_IMAGE_DIGEST=/ { print "CINEGRAPH_IMAGE_DIGEST=" digest; digest_found = 1; next }
    /^CINEGRAPH_RELEASE_SHA=/ { print "CINEGRAPH_RELEASE_SHA=" release_sha; sha_found = 1; next }
    { print }
    END { if (!image_found || !digest_found || !sha_found) exit 21 }
' "$ENV_FILE" > "$candidate_env" || fail "Dev environment image contract is invalid"

candidate_compose="$release_dir/deploy/compose.yaml"
[[ -f "$candidate_compose" ]] || fail "release Compose file is missing"
PYTHONPATH="$release_dir" python3 "$release_dir/scripts/validate_vps_runtime.py" \
    --environment development \
    --env-file "$candidate_env" \
    --compose-file "$candidate_compose" \
    --allow-active-port

compose=(docker compose --env-file "$candidate_env" -f "$candidate_compose")
# The previous env is checked before any pull, migration, or provisioning so a
# prior failed promotion cannot cause another database mutation on retry.
[[ ! -e "$previous_env" && ! -L "$previous_env" ]] || fail "previous Dev environment backup exists; recover before retry"
"${compose[@]}" pull app postgres qdrant
"${compose[@]}" up -d postgres qdrant
"${compose[@]}" --profile migration run --rm migrate
"${compose[@]}" --profile provisioning run --rm provision-qdrant

# Only after all pre-start checks and one-shot operations pass do we replace the
# operator-owned env file and selected release pointer. Never auto-downgrade a
# database migration if the subsequent app health check fails.
final_env="$(mktemp /etc/cinegraph/dev.env.final.XXXXXX)"
deployment_succeeded=0
trap 'rm -f "$candidate_env" "$final_env"; if [[ "$deployment_succeeded" -eq 1 ]]; then rm -f "$previous_env"; fi' EXIT
[[ ! -e "$previous_env" ]] || fail "previous Dev environment backup exists; recover before retry"
cp --preserve=mode "$ENV_FILE" "$previous_env"
chmod 600 "$previous_env"
sed "s#^CINEGRAPH_ENV_FILE=.*#CINEGRAPH_ENV_FILE=$ENV_FILE#" "$candidate_env" > "$final_env"
chmod 600 "$final_env"
mv -f "$final_env" "$ENV_FILE"

temporary_link="$DEPLOY_ROOT/.current-$release_sha"
ln -sfn "$release_dir" "$temporary_link"
mv -Tf "$temporary_link" "$CURRENT_LINK"

current_compose=(docker compose --env-file "$ENV_FILE" -f "$CURRENT_LINK/deploy/compose.yaml")
"${current_compose[@]}" up -d app
ready=0
for _ in {1..12}; do
    if curl --fail --silent --show-error --connect-timeout 5 --max-time 15 \
        "http://127.0.0.1:$DEV_COMPOSE_PORT/health/ready" >/dev/null; then
        ready=1
        break
    fi
    sleep 5
done
[[ "$ready" -eq 1 ]] || fail "Dev readiness check did not pass within the bounded window"

deployment_succeeded=1
