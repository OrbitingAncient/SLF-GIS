"""Load every dashboard tab in Chromium and screenshot it."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[1] / "data" / "processed" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)
URL = "http://127.0.0.1:8000"
TABS = ["Harvest", "Overview", "Phenology", "Bloom", "Weather", "Frost", "Pests", "Sources", "Reports"]


def main() -> int:
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
        page.on("console", lambda msg: errors.append(f"console.{msg.type}: {msg.text}") if msg.type in ("error", "warning") else None)

        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector(".navbar", timeout=30000)
        page.wait_for_timeout(4000)

        for tab in TABS:
            loc = page.locator(".nav-link", has_text=tab)
            loc.first.click()
            page.wait_for_timeout(4000)
            # Expand closed accordions on this tab
            for acc in page.locator(".accordion-button.collapsed").all():
                try:
                    acc.click()
                    page.wait_for_timeout(800)
                except Exception:
                    pass
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(400)
            page.screenshot(path=str(OUT / f"{tab.lower()}.png"), full_page=True)
            print(f"shot {tab}")

        # Harvest plot size check
        page.get_by_role("link", name="Harvest", exact=True).first.click()
        page.wait_for_timeout(2000)
        sizes = page.evaluate(
            """() => Array.from(document.querySelectorAll('.shiny-plot-output')).map(el => ({
                id: el.id, w: el.clientWidth, h: el.clientHeight
            }))"""
        )
        print("plot sizes", sizes)
        for el in sizes:
            if el["h"] < 200:
                errors.append(f"plot {el['id']} height {el['h']}")

        # Change region
        page.select_option("#region", label="Berrien / Benton Harbor")
        page.wait_for_timeout(2500)
        page.screenshot(path=str(OUT / "harvest_berrien.png"), full_page=True)
        print("shot Berrien")

        page.select_option("#season", label="2025")
        page.wait_for_timeout(2500)
        page.screenshot(path=str(OUT / "harvest_2025.png"), full_page=True)
        print("shot 2025")

        browser.close()

    print("errors", len(errors))
    for e in errors:
        print("ERR", e)
    return 1 if any("pageerror" in e or "console.error" in e or "height" in e for e in errors) else 0


if __name__ == "__main__":
    sys.exit(main())
