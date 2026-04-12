"""FastAPI app factory for the trading dashboard."""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .database import get_db, load_settings, save_settings
from .scanner_service import ScannerService

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    # Database
    conn = get_db()
    app.state.db = conn

    # Settings
    settings = load_settings(conn)
    app.state.settings = settings

    # Scanner service
    scanner = ScannerService(settings)
    app.state.scanner = scanner

    # Scheduler
    scheduler = AsyncIOScheduler()

    async def weather_job():
        try:
            await scanner.scan_weather()
        except Exception as exc:
            logger.error("Weather scan job failed: %s", exc)

    async def kalshi_job():
        try:
            await scanner.scan_kalshi()
        except Exception as exc:
            logger.error("Kalshi scan job failed: %s", exc)

    async def arb_job():
        try:
            await scanner.scan_arbitrage()
        except Exception as exc:
            logger.error("Arb scan job failed: %s", exc)

    scheduler.add_job(weather_job, "interval", minutes=settings.weather_scan_interval_min, id="weather")
    scheduler.add_job(kalshi_job, "interval", minutes=settings.kalshi_scan_interval_min, id="kalshi")
    scheduler.add_job(arb_job, "interval", minutes=settings.arb_scan_interval_min, id="arb")
    scheduler.start()
    app.state.scheduler = scheduler

    # Run initial weather scan on startup
    asyncio.create_task(weather_job())

    yield

    # Shutdown
    scheduler.shutdown()
    conn.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Polyphemus Trading Dashboard", lifespan=lifespan)

    # Templates
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.state.templates = templates

    # Custom template filters
    def format_pct(value):
        if value is None:
            return "-"
        return f"{value:+.1%}" if abs(value) < 1 else f"{value:+.1f}%"

    def format_price(value):
        if value is None:
            return "-"
        return f"${value:.3f}" if value < 1 else f"${value:.2f}"

    def format_edge(value):
        if value is None:
            return "-"
        return f"{value:+.1%}"

    templates.env.filters["pct"] = format_pct
    templates.env.filters["price"] = format_price
    templates.env.filters["edge"] = format_edge

    # Static files
    STATIC_DIR.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Routes
    from .routes.dashboard import router as dashboard_router
    from .routes.opportunity import router as opportunity_router
    from .routes.portfolio import router as portfolio_router
    from .routes.settings import router as settings_router
    from .routes.api import router as api_router

    app.include_router(dashboard_router)
    app.include_router(opportunity_router)
    app.include_router(portfolio_router)
    app.include_router(settings_router)
    app.include_router(api_router, prefix="/api")

    return app
