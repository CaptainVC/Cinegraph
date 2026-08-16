SPEAKER_REVIEW_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidate_id": {"type": "string"},
        "action": {
            "type": "string",
            "enum": ["accept_candidate", "correct_candidate", "needs_review"],
        },
        "speaker": {"type": "string"},
        "confidence": {"type": "number"},
        "evidence_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "rationale": {"type": "string"},
    },
    "required": [
        "candidate_id",
        "action",
        "speaker",
        "confidence",
        "evidence_ids",
        "rationale",
    ],
    "additionalProperties": False,
}
