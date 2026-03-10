#!/usr/bin/env python3
"""
gumroad_upload_thumbnail.py — Programmatically upload a thumbnail to a Gumroad product.

Uses Playwright to bypass the native OS file dialog limitation.
set_input_files() injects the file directly onto the <input type="file"> element.

Usage:
    python3 gumroad_upload_thumbnail.py <product_id> <image_path>

Example:
    python3 gumroad_upload_thumbnail.py zkgzw ~/Desktop/cover.png

Requires:
    pip install playwright
    playwright install chromium
"""

import asyncio
import sys
from pathlib import Path


async def upload_thumbnail(product_id: str, image_path: str, headless: bool = False):
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("ERROR: playwright not installed. Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    image_path = str(Path(image_path).expanduser().resolve())
    if not Path(image_path).exists():
        print(f"ERROR: Image file not found: {image_path}")
        sys.exit(1)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()

        # Open login page and wait for manual login
        print("Opening Gumroad login page...")
        await page.goto("https://app.gumroad.com/login")
        print("\nLog in manually in the browser window.")
        print("Waiting for dashboard URL (2 min timeout)...")
        await page.wait_for_url("**/dashboard**", timeout=120000)
        print("Logged in. Continuing...")

        # Navigate to product edit
        edit_url = f"https://app.gumroad.com/products/{product_id}/edit"
        print(f"Navigating to {edit_url}")
        await page.goto(edit_url)
        await page.wait_for_load_state("networkidle")

        # Scroll to thumbnail section
        await page.evaluate("window.scrollBy(0, 400)")
        await page.wait_for_timeout(500)

        # Find the thumbnail file input (first image file input)
        # Gumroad has: input[accept=".jpeg,.jpg,.png,.gif,.webp"]
        thumbnail_input = page.locator('input[accept*=".png"]').nth(0)

        # THE KEY: set_input_files bypasses native OS dialog entirely
        print(f"Setting thumbnail: {image_path}")
        await thumbnail_input.set_input_files(image_path)

        # Wait for upload to process (Gumroad shows preview)
        await page.wait_for_timeout(3000)

        # Save changes
        save_btn = page.locator('button:has-text("Save changes")')
        await save_btn.click()
        await page.wait_for_timeout(2000)

        # Verify save
        # Gumroad shows a toast or the preview updates
        print("Saved. Verifying...")
        await page.screenshot(path="/tmp/gumroad_thumbnail_upload_result.png")
        print("Screenshot saved to /tmp/gumroad_thumbnail_upload_result.png")

        await browser.close()
        print("Done.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    product_id = sys.argv[1]
    image_path = sys.argv[2]
    asyncio.run(upload_thumbnail(product_id, image_path))
