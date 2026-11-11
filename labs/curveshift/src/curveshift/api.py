"""An optional read-only research API; computations use the same frozen source."""

import math
from datetime import date

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .cli import evaluate_scenario
from .market import load_market

app = FastAPI(
    title="CurveShift", version="0.1.0", description="Frozen historical Treasury-curve scenarios, not live quotes"
)


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    as_of: str = "2025-12-31"
    shock_bp: list[float] = Field(min_length=4, max_length=4)
    source_sha256: str | None = None

    @field_validator("as_of")
    @classmethod
    def valid_date(cls, value):
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("Use YYYY-MM-DD")
        return value

    @field_validator("shock_bp", mode="before")
    @classmethod
    def valid_numbers(cls, value):
        if not isinstance(value, list) or any(
            type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 300 for x in value
        ):
            raise ValueError("Four numeric basis-point shifts in [-300,300] are required")
        return value


@app.get("/health")
def health():
    frame, source = load_market()
    return {
        "status": "ok",
        "observations": frame.height,
        "source_sha256": source["subset_sha256"],
        "start": source["start"],
        "end": source["end"],
    }


@app.post("/scenario")
def reprice(request: ScenarioRequest):
    _, source = load_market()
    inputs = {
        "as_of": request.as_of,
        "shock_bp": request.shock_bp,
        "source_sha256": request.source_sha256 or source["subset_sha256"],
    }
    try:
        return evaluate_scenario(inputs)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
