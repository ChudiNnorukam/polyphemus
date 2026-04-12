"""Portfolio route - paper trades and P&L."""
from fastapi import APIRouter, Request

from ..database import get_open_trades, get_resolved_trades, get_portfolio_summary

router = APIRouter()


@router.get("/portfolio")
async def portfolio(request: Request):
    conn = request.app.state.db
    return request.app.state.templates.TemplateResponse(
        "portfolio.html",
        {
            "request": request,
            "open_trades": get_open_trades(conn),
            "resolved_trades": get_resolved_trades(conn),
            "summary": get_portfolio_summary(conn),
            "active_page": "portfolio",
        },
    )
