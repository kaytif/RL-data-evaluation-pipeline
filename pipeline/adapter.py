from typing import Protocol
from pydantic import BaseModel, Field

class DraftInput(BaseModel):
    brief: str = Field(min_length=1, max_length=20000)

class DraftOutput(BaseModel):
    simulated: bool = True
    requirement: str
    clarification: str

class Adapter(Protocol):
    def draft(self, request: DraftInput) -> DraftOutput: ...

class FakeAdapter:
    """One deterministic call, no tools, retries, tokens, or API credentials."""
    instructions = 'Propose the bundled CSV criterion; ask a human to confirm applicability.'
    def draft(self, request: DraftInput) -> DraftOutput:
        return DraftOutput(requirement='Accept correct settled-transaction CSVs, including reordered rows; reject empty, missing, malformed and incorrect outputs.',
            clarification='This simulated draft does not interpret arbitrary briefs. Confirm that the bundled settled-transaction task and all listed probes match your brief before approving.')
