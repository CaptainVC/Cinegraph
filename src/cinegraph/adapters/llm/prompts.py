from langchain_core.prompts import ChatPromptTemplate

from cinegraph.common.prompts import (
    GROUNDED_ANSWER_HUMAN_PROMPT,
    GROUNDED_ANSWER_SYSTEM_PROMPT,
)


# Build the system and human messages used for grounded, citation-bearing answers.
def build_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages(
        [
            ("system", GROUNDED_ANSWER_SYSTEM_PROMPT),
            ("human", GROUNDED_ANSWER_HUMAN_PROMPT),
        ]
    )
