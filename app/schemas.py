from datetime import date as Date
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class SlotStatus(StrEnum):
    AVAILABLE = "available"
    BOOKED = "booked"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class BookingStatus(StrEnum):
    RESERVED = "reserved"
    FAILED = "failed"
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


class BookingPlayer(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr | None = None
    mobile_number: str | None = Field(default=None, max_length=30)


class TeeSheetBookingRequest(BaseModel):
    date: Date
    side: Literal["front", "back"]
    time: str = Field(..., description="Requested tee time, including AM or PM.")
    holes: Literal[9, 18]
    players: Annotated[list[BookingPlayer], Field(min_length=1, max_length=4)]
    dry_run: bool = Field(
        default=False,
        description="Fill the booking form without pressing Reserve.",
    )

    @field_validator("time")
    @classmethod
    def validate_time_has_meridiem(cls, value: str) -> str:
        normalized = " ".join(value.strip().upper().split())
        if not normalized.endswith((" AM", " PM")):
            raise ValueError("time must include AM or PM")
        return normalized


class TeeSheetBookingResponse(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    date: Date
    side: Literal["front", "back"]
    time: str
    holes: Literal[9, 18]
    player_count: int
    status: BookingStatus
    message: str
    warnings: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: Literal["ok"]
    app: str
