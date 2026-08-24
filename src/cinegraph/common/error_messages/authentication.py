class AuthenticationErrorMessages:
    EMAIL_ADDRESS_MUST_BE_VALID = "Email address must be valid."
    DISPLAY_NAME_MUST_BE_TRIMMED = "Display name must be non-empty and trimmed."
    PASSWORD_LENGTH_MUST_BE_VALID = (
        "Password length must satisfy the configured bounds."
    )
    PASSWORD_HASH_MUST_BE_VALID = "Stored password hash must be valid."
    SESSION_TOKEN_DIGEST_MUST_BE_VALID = "Session token digest must be valid SHA-256."
    SESSION_EXPIRY_MUST_FOLLOW_CREATION = "Session expiry must follow creation time."
    SESSION_REVOCATION_MUST_NOT_PREDATE_CREATION = (
        "Session revocation must not predate creation."
    )
    EMAIL_ALREADY_REGISTERED = "An account with this email address already exists."
    INVALID_CREDENTIALS = "Email address or password is invalid."
    ACCOUNT_DISABLED = "This account is disabled."
    SESSION_INVALID = "Session is invalid or expired."
    SESSION_TOKEN_MUST_BE_VALID = "Session token must be non-empty and trimmed."
    SESSION_PRINCIPAL_MUST_MATCH_KIND = (
        "Session principal identity and corpus scope must match its kind."
    )
    ACCOUNT_REQUIRED = "An authenticated account is required."
    PASSWORD_MUST_DIFFER = "New password must differ from the current password."
    SESSION_NOT_FOUND = "Session was not found."
    CSRF_TOKEN_REQUIRED = "CSRF protection failed."
    SAME_ORIGIN_REQUIRED = "Same-origin request required."
