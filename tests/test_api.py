from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from app.main import app, get_automation
from app.schemas import BookingStatus, SlotStatus, TeeSheetBookingResponse, TeeSheetResponse, TeeSlot


class FakeAutomation:
    def fetch_tee_sheet(self, target_date: date) -> TeeSheetResponse:
        return TeeSheetResponse(
            date=target_date,
            club_code="CC18",
            front=[
                TeeSlot(
                    time="6:44 AM",
                    side="front",
                    status=SlotStatus.AVAILABLE,
                    raw_text="6:44 AM | Add",
                )
            ],
            back=[
                TeeSlot(
                    time="4:44 PM",
                    side="back",
                    status=SlotStatus.BOOKED,
                    raw_text="4:44 PM | Example Player",
                    player_or_note="Example Player",
                )
            ],
            extracted_at=datetime(2026, 5, 6, 7, 0, tzinfo=UTC),
        )

    def book_tee_time(self, booking) -> TeeSheetBookingResponse:
        return TeeSheetBookingResponse(
            date=booking.date,
            side=booking.side,
            time=booking.time,
            holes=booking.holes,
            player_count=len(booking.players),
            status=BookingStatus.RESERVED,
            message="Reservation submitted successfully.",
        )


def test_health_endpoint() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app": "golf_club_automation"}


def test_query_tee_sheet_uses_automation_dependency() -> None:
    app.dependency_overrides[get_automation] = FakeAutomation
    client = TestClient(app)

    response = client.post("/tee-sheet/query", json={"date": "2026-10-05"})

    app.dependency_overrides.clear()
    assert response.status_code == 200
    payload = response.json()
    assert payload["date"] == "2026-10-05"
    assert payload["club_code"] == "CC18"
    assert payload["front"][0]["status"] == "available"
    assert payload["back"][0]["player_or_note"] == "Example Player"


def test_query_tee_sheet_rejects_invalid_date() -> None:
    client = TestClient(app)

    response = client.post("/tee-sheet/query", json={"date": "not-a-date"})

    assert response.status_code == 422


def test_book_tee_sheet_uses_automation_dependency() -> None:
    app.dependency_overrides[get_automation] = FakeAutomation
    client = TestClient(app)

    response = client.post(
        "/tee-sheet/book",
        json={
            "date": "2026-10-05",
            "side": "front",
            "time": "7:00 AM",
            "holes": 9,
            "players": [
                {
                    "first_name": "Ashwin",
                    "last_name": "Test",
                    "email": "ashwin@example.com",
                    "mobile_number": "1234567890",
                }
            ],
            "dry_run": True,
        },
    )

    app.dependency_overrides.clear()
    assert response.status_code == 200
    payload = response.json()
    assert payload["date"] == "2026-10-05"
    assert payload["side"] == "front"
    assert payload["holes"] == 9
    assert payload["player_count"] == 1
    assert payload["status"] == "reserved"


def test_book_tee_sheet_rejects_invalid_player_count() -> None:
    client = TestClient(app)

    response = client.post(
        "/tee-sheet/book",
        json={
            "date": "2026-10-05",
            "side": "front",
            "time": "7:00 AM",
            "holes": 9,
            "players": [],
        },
    )

    assert response.status_code == 422
