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
