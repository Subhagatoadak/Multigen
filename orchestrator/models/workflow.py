from pydantic import BaseModel


class RunRequest(BaseModel):
    dsl: dict  # JSON/YAML parsed as Python dict

class RunResponse(BaseModel):
    instance_id: str
