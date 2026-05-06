from fastapi import Depends, FastAPI, HTTPException, status

from app.automation.club_caddie import ClubCaddieAutomationError
from app.schemas import HealthResponse, TeeSheetQuery, TeeSheetResponse
from app.services.tee_sheet import (
    TeeSheetAutomation,
    build_automation,
    fetch_tee_sheet_for_date,
)

app = FastAPI(
    title="Golf Club Automation API",
    version="0.1.0",
    description="Automates Club Caddie GMS tee sheet lookup for a requested date.",
)


def get_automation() -> TeeSheetAutomation:
    return build_automation()


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", app="golf_club_automation")


@app.post("/tee-sheet/query", response_model=TeeSheetResponse)
async def query_tee_sheet(
    payload: TeeSheetQuery,
    automation: TeeSheetAutomation = Depends(get_automation),
) -> TeeSheetResponse:
    try:
        return await fetch_tee_sheet_for_date(payload.date, automation)
    except ClubCaddieAutomationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
