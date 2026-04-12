"""Entry point for the Polyphemus trading dashboard."""
import logging
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from .app import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "polyphemus.prediction_markets.webapp.run:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
