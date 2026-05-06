import asyncio
from datetime import UTC, date, datetime
from typing import Protocol

from app.automation.club_caddie import ClubCaddieAutomation
from app.config import Settings, get_settings
from app.schemas import TeeSheetResponse


class TeeSheetAutomation(Protocol):
    def fetch_tee_sheet(self, target_date: date) -> TeeSheetResponse:
        """Return the tee sheet from the desktop application."""


_automation_lock = asyncio.Lock()


def build_automation(settings: Settings | None = None) -> TeeSheetAutomation:
    return ClubCaddieAutomation(settings or get_settings())


async def fetch_tee_sheet_for_date(
    target_date: date,
    automation: TeeSheetAutomation | None = None,
) -> TeeSheetResponse:
    selected_automation = automation or build_automation()

    async with _automation_lock:
        response = await asyncio.to_thread(
            selected_automation.fetch_tee_sheet,
            target_date,
        )

    if response.extracted_at.tzinfo is None:
        response.extracted_at = response.extracted_at.replace(tzinfo=UTC)
    return response


def empty_response(target_date: date, club_code: str, warning: str) -> TeeSheetResponse:
    return TeeSheetResponse(
        date=target_date,
        club_code=club_code,
        front=[],
        back=[],
        extracted_at=datetime.now(UTC),
        warnings=[warning],
    )
