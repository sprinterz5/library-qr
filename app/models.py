from pydantic import BaseModel, Field

class ReturnRequest(BaseModel):
    barcode: str = Field(..., min_length=3)

class IssueRequest(BaseModel):
    barcode: str = Field(..., min_length=3)
    student_id: str = Field(..., min_length=1)

class UiSubmitRequest(BaseModel):
    barcode: str
    student_id: str | None = None
    action: str  # "issue" or "return"
