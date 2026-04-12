"""API routes - HTMX partials, SSE, order book, paper trading."""
import asyncio
import json
import logging

import httpx
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from ..database import get_open_trades, get_resolved_trades, get_portfolio_summary

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/opportunities")
async def get_opportunities(request: Request, scanner: str | None = None, sort: str = "ev_net"):
    """Return opportunity cards as HTML partial (HTMX target)."""
    service = request.app.state.scanner
    opps = service.get_all_opportunities(scanner_filter=scanner, sort_by=sort)

    return request.app.state.templates.TemplateResponse(
        "partials/opportunity_list.html",
        {"request": request, "opportunities": opps},
    )


@router.get("/events")
async def sse_events(request: Request):
    """Server-sent events for live dashboard updates."""
    service = request.app.state.scanner
    queue = service.subscribe()

    async def event_generator():
        try:
            while True:
                event_type = await asyncio.wait_for(queue.get(), timeout=30)
                if event_type == "scan_complete":
                    opps = service.get_all_opportunities()
                    html = request.app.state.templates.get_template(
                        "partials/opportunity_list.html"
                    ).render({"request": request, "opportunities": opps})
                    yield {"event": "scan_complete", "data": html}

                    status_html = request.app.state.templates.get_template(
                        "partials/scanner_status.html"
                    ).render({"scanner_status": service.get_scanner_status()})
                    yield {"event": "scanner_status", "data": status_html}
        except asyncio.TimeoutError:
            yield {"event": "heartbeat", "data": ""}
        except asyncio.CancelledError:
            pass
        finally:
            service.unsubscribe(queue)

    return EventSourceResponse(event_generator())


@router.get("/orderbook/{token_id}")
async def get_orderbook(token_id: str):
    """Fetch live order book from Polymarket CLOB and render as HTML."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://clob.polymarket.com/book",
                params={"token_id": token_id},
            )
            resp.raise_for_status()
            book = resp.json()
    except Exception as exc:
        return HTMLResponse(f'<p class="text-gray-500 text-sm">Order book unavailable: {exc}</p>')

    bids = book.get("bids", [])[:8]
    asks = book.get("asks", [])[:8]
    spread = book.get("spread", 0)
    midpoint = book.get("midpoint", 0)

    max_size = max(
        max((float(b.get("size", 0)) for b in bids), default=1),
        max((float(a.get("size", 0)) for a in asks), default=1),
    )

    html = f'<div class="text-xs text-gray-500 mb-2">Spread: ${spread:.4f} | Mid: ${midpoint:.4f}</div>'

    # Asks (reversed so lowest ask is at bottom, near spread)
    html += '<div class="space-y-0.5 mb-1">'
    for a in reversed(asks):
        price = float(a.get("price", 0))
        size = float(a.get("size", 0))
        pct = (size / max_size * 100) if max_size > 0 else 0
        html += (
            f'<div class="flex items-center gap-2 text-xs">'
            f'<span class="w-14 text-right text-sell font-mono">${price:.3f}</span>'
            f'<div class="flex-1 h-4 bg-gray-800 rounded overflow-hidden">'
            f'<div class="h-full bg-sell/30 rounded" style="width:{pct:.0f}%"></div></div>'
            f'<span class="w-16 text-right text-gray-400 font-mono">{size:.0f}</span>'
            f'</div>'
        )
    html += '</div>'

    # Spread line
    html += '<div class="border-t border-gray-700 my-1"></div>'

    # Bids
    html += '<div class="space-y-0.5">'
    for b in bids:
        price = float(b.get("price", 0))
        size = float(b.get("size", 0))
        pct = (size / max_size * 100) if max_size > 0 else 0
        html += (
            f'<div class="flex items-center gap-2 text-xs">'
            f'<span class="w-14 text-right text-buy font-mono">${price:.3f}</span>'
            f'<div class="flex-1 h-4 bg-gray-800 rounded overflow-hidden">'
            f'<div class="h-full bg-buy/30 rounded" style="width:{pct:.0f}%"></div></div>'
            f'<span class="w-16 text-right text-gray-400 font-mono">{size:.0f}</span>'
            f'</div>'
        )
    html += '</div>'

    return HTMLResponse(html)


@router.post("/trade")
async def paper_trade(request: Request, opportunity_id: str = Form(...)):
    """Record a paper trade from an opportunity."""
    service = request.app.state.scanner
    all_opps = service.get_all_opportunities()
    opp = next((o for o in all_opps if o.id == opportunity_id), None)

    if not opp:
        return HTMLResponse(
            '<div class="toast bg-red-600 text-white px-4 py-2 rounded-lg text-sm font-medium shadow-lg">'
            'Opportunity not found</div>'
        )

    conn = request.app.state.db
    settings = request.app.state.settings

    from ..weather.paper_tracker import record_trade
    try:
        trade_id = record_trade(
            conn=conn,
            city=opp.city or "",
            market_date=opp.market_date or "",
            temp=opp.temp or 0,
            direction=opp.direction,
            market_price=opp.market_price,
            forecast_prob=opp.forecast_prob or 0,
            edge=opp.edge,
            ev_net=opp.ev_net,
            kelly=opp.kelly,
            unit=opp.unit or "C",
            question_type=opp.question_type or "bucket",
            question=opp.question,
            forecast_temp=opp.forecast_temp,
            token_id=opp.token_id,
            bankroll=settings.bankroll,
        )
        return HTMLResponse(
            f'<div class="toast bg-emerald-600 text-white px-4 py-2 rounded-lg text-sm font-medium shadow-lg">'
            f'Paper trade #{trade_id} recorded</div>'
        )
    except Exception as exc:
        return HTMLResponse(
            f'<div class="toast bg-red-600 text-white px-4 py-2 rounded-lg text-sm font-medium shadow-lg">'
            f'Error: {exc}</div>'
        )


@router.post("/portfolio/resolve/{trade_id}")
async def resolve_trade(trade_id: int, request: Request, outcome: str = Form(...)):
    """Resolve a paper trade."""
    conn = request.app.state.db
    from ..weather.paper_tracker import resolve_trade as _resolve
    try:
        pnl = _resolve(conn, trade_id, outcome)
    except Exception as exc:
        return HTMLResponse(
            f'<div class="toast bg-red-600 text-white px-4 py-2 rounded-lg text-sm">'
            f'Error: {exc}</div>'
        )

    # Return updated portfolio content
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
