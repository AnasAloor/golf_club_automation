from datetime import date as Date
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SlotStatus(StrEnum):
    AVAILABLE = "available"
    BOOKED = "booked"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class TeeSheetQuery(BaseModel):
    date: Date = Field(..., description="Date to load in the Club Caddie tee sheet.")


class TeeSlot(BaseModel):
    time: str = Field(..., description="Visible tee time label from the tee sheet.")
    status: SlotStatus = Field(..., description="Normalized slot availability status.")
    side: Literal["front", "back"]
    raw_text: str = Field(..., description="Raw UI text used to classify the slot.")
    player_or_note: str | None = Field(
        default=None,
        description="Visible booking name, block note, or other tee sheet text.",
    )
    source: Literal["uia", "screenshot", "manual"] = "uia"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

class TeeSheetResponse(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    date: Date
    club_code: str
    front: list[TeeSlot]
    back: list[TeeSlot]
    extracted_at: datetime
    warnings: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: Literal["ok"]
    app: str
