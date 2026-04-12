"""Opportunity detail route."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/opportunity/{opp_id}")
async def opportunity_detail(opp_id: str, request: Request):
    scanner = request.app.state.scanner
    all_opps = scanner.get_all_opportunities()

    opp = next((o for o in all_opps if o.id == opp_id), None)
    if not opp:
        return HTMLResponse("<h1>Opportunity not found</h1>", status_code=404)

    return request.app.state.templates.TemplateResponse(
        "opportunity_detail.html",
        {"request": request, "opp": opp, "active_page": "dashboard"},
    )
