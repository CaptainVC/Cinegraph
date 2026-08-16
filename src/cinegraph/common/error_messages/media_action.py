class MediaActionErrorMessages:
    COMMAND_IDS_MUST_BE_VALID = "Media command identifiers must be UUID values."
    COMMAND_REVISION_MUST_BE_TRIMMED = (
        "Media command provider revision must be non-empty and trimmed."
    )
    IDEMPOTENCY_KEY_MUST_BE_TRIMMED = (
        "Media command idempotency key must be non-empty and trimmed."
    )
    COMMAND_EPISODES_MUST_BE_IMMUTABLE = (
        "Media command episode IDs must be an immutable tuple."
    )
    COMMAND_EPISODES_MUST_BE_UNIQUE = (
        "Media command episode IDs must be unique."
    )
    COMMAND_PARAMETERS_MUST_MATCH_KIND = (
        "Media command parameters must match the command kind."
    )
    PLAYLIST_NAME_MUST_BE_SAFE = "Playlist name must be non-empty and trimmed."
    AUTHENTICATED_PRINCIPAL_REQUIRED = (
        "Media actions require an authenticated principal."
    )
    PRINCIPAL_MUST_OWN_PROFILE = (
        "Media action principal must own the target profile."
    )
    PRINCIPAL_MUST_OWN_PROVIDER = (
        "Media action principal must own the provider connection."
    )
    COMMAND_KIND_NOT_ALLOWED = "Media command kind is not allowed."
    PROVIDER_CONNECTION_CHANGED = (
        "Provider connection changed after the command was prepared."
    )
    APPROVAL_NOT_FOUND = "Media action approval was not found."
    APPROVAL_EXPIRED = "Media action approval has expired."
    APPROVAL_COMMAND_MISMATCH = (
        "Approval applies only to the exact previewed command."
    )
    APPROVAL_TRANSITION_INVALID = "Media action approval transition is invalid."
    IDEMPOTENCY_KEY_REUSED = (
        "Media action idempotency key was reused for a different command."
    )
    PROVIDER_VERIFICATION_FAILED = (
        "Media provider action could not be verified."
    )
    WORKFLOW_RESULT_MUST_EXIST = "Media action workflow completed without state."
