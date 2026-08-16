class WorkflowErrorMessages:
    AGENT_RUNTIME_CONTEXT_MUST_BE_VALID = "Agent runtime context must be valid."
    GROUNDED_ANSWER_TOOL_UNAVAILABLE = (
        "Grounded evidence is unavailable; the assistant must not answer from memory."
    )
    MAX_REGENERATION_ATTEMPTS_MUST_BE_NON_NEGATIVE = (
        "Maximum regeneration attempts must be non-negative."
    )
    COMPLETED_WORKFLOW_RESULT_CANNOT_BE_NONE = (
        "Completed workflow must have a result."
    )
    SPEAKER_REVIEW_GRAPH_RESULT_REQUIRED = (
        "Speaker review graph must finish with a run directory and state."
    )
    SPEAKER_REVIEW_CORPUS_ROOT_REQUIRED = (
        "Speaker review graph start requires a corpus root."
    )
    SPEAKER_REVIEW_RUN_DIRECTORY_REQUIRED = (
        "Speaker review graph resume requires a run directory."
    )
