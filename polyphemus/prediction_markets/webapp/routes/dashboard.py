"""Dashboard route - main opportunity scanner page."""
from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/")
async def dashboard(request: Request):
    scanner = request.app.state.scanner
    opportunities = scanner.get_all_opportunities()
    scanner_status = scanner.get_scanner_status()

    return request.app.state.templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "opportunities": opportunities,
            "scanner_status": scanner_status,
            "active_page": "dashboard",
        },
    )
