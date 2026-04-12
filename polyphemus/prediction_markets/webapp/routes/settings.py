"""Settings route."""
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse

from ..database import load_settings, save_settings
from ..models import AppSettings

router = APIRouter()


@router.get("/settings")
async def settings_page(request: Request):
    settings = load_settings(request.app.state.db)
    return request.app.state.templates.TemplateResponse(
        "settings.html",
        {"request": request, "settings": settings, "active_page": "settings"},
    )


@router.post("/settings")
async def save_settings_form(
    request: Request,
    bankroll: float = Form(200.0),
    max_total_deployment_pct: float = Form(50.0),
    max_single_position_pct: float = Form(5.0),
    max_positions_per_city_date: int = Form(3),
    weather_threshold: float = Form(0.10),
    weather_min_ev: float = Form(0.01),
    weather_min_kelly: float = Form(0.05),
    weather_scan_interval_min: int = Form(5),
    arb_min_spread: float = Form(0.01),
    arb_scan_interval_min: int = Form(15),
):
    settings = AppSettings(
        bankroll=bankroll,
        max_total_deployment_pct=max_total_deployment_pct / 100,
        max_single_position_pct=max_single_position_pct / 100,
        max_positions_per_city_date=max_positions_per_city_date,
        weather_threshold=weather_threshold,
        weather_min_ev=weather_min_ev,
        weather_min_kelly=weather_min_kelly,
        weather_scan_interval_min=weather_scan_interval_min,
        arb_min_spread=arb_min_spread,
        arb_scan_interval_min=arb_scan_interval_min,
    )
    save_settings(request.app.state.db, settings)
    request.app.state.settings = settings
    request.app.state.scanner.settings = settings

    return HTMLResponse(
        '<div class="toast bg-emerald-600 text-white px-4 py-2 rounded-lg text-sm font-medium shadow-lg">'
        'Settings saved</div>'
    )
