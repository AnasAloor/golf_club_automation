from datetime import UTC, date, datetime

from app.schemas import SlotStatus, TeeSheetResponse, TeeSlot


def test_tee_sheet_response_serializes_enum_values() -> None:
    response = TeeSheetResponse(
        date=date(2026, 10, 5),
        club_code="CC18",
        front=[
            TeeSlot(
                time="6:44 AM",
                side="front",
                status=SlotStatus.AVAILABLE,
                raw_text="6:44 AM | Add",
            )
        ],
        back=[],
        extracted_at=datetime(2026, 5, 6, tzinfo=UTC),
    )

    payload = response.model_dump(mode="json")

    assert payload["front"][0]["status"] == "available"
    assert payload["date"] == "2026-10-05"
