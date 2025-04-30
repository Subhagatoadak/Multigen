from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

class Capability(BaseModel):
    name: str = Field(
        ...,
        json_schema_extra={"example": "EchoAgent"},
    )
    version: str = Field(
        ...,
        json_schema_extra={"example": "1.0.0"},
    )
    description: Optional[str] = Field(
        None,
        json_schema_extra={"example": "Echo agent for testing"},
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
