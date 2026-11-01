"""Re-capture docs/screenshots/*.png from a live local dashboard (headless system Chrome).

Uses the real server with your real toolchain (XC16, packs, datasheet, API key
presence shown only as "set" pills - keys are never echoed by the UI), so the
shots always match the shipped product. No API calls are made.

Usage (Windows PowerShell, from repo root):
    .venv\\Scripts\\pip.exe install playwright        # once (driver only; uses system Chrome)
    .venv\\Scripts\\python.exe scripts\\capture_screenshots.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "docs" / "screenshots"
PORT = 8123
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def api(path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=20) as r:
        return json.load(r)


def main() -> None:
    from playwright.sync_api import sync_playwright

    SHOTS.mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "chipllama.server:get_app", "--factory",
         "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                api("/api/status")
                break
            except OSError:
                time.sleep(0.5)
        else:
            sys.exit("server did not start")
        audits = [x["name"] for x in api("/api/audit/examples")]
        blocks = [e["name"] for e in api("/api/blocks/meta")["examples"] if not e.get("family")]
        aex = "blinky_buggy" if "blinky_buggy" in audits else audits[0]
        bex = "blink" if "blink" in blocks else blocks[0]
        print("audit example:", aex, "| blocks example:", bex)
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=CHROME, headless=True)
            pg = b.new_page(viewport={"width": 1440, "height": 900})
            pg.goto(f"http://127.0.0.1:{PORT}/")
            pg.wait_for_timeout(2500)
            pg.fill("#req", "Timer1 interrupt every 10 ms, priority 5")
            pg.wait_for_timeout(600)
            pg.screenshot(path=str(SHOTS / "generate.png"))
            pg.evaluate("tab('aud')")
            pg.wait_for_timeout(800)
            pg.evaluate(f"Audit.loadEx({aex!r})")
            pg.wait_for_selector(".ascore", timeout=30000)
            pg.wait_for_timeout(800)
            pg.screenshot(path=str(SHOTS / "audit.png"))
            pg.evaluate("Audit.explain()")
            pg.wait_for_timeout(2500)
            pg.screenshot(path=str(SHOTS / "explain.png"))
            pg.evaluate("tab('pin')")
            pg.wait_for_selector("#pinout .card", timeout=30000)
            pg.wait_for_timeout(800)
            pg.screenshot(path=str(SHOTS / "pins.png"))
            pg.evaluate("tab('blk')")
            pg.evaluate("Blocks.init()")
            pg.wait_for_selector("#bws svg", timeout=30000)
            pg.evaluate(f"Blocks.loadEx({bex!r})")
            pg.wait_for_timeout(3500)
            pg.screenshot(path=str(SHOTS / "blocks.png"))
            b.close()
    finally:
        server.terminate()
    for f in ("generate", "audit", "explain", "pins", "blocks"):
        print("wrote", SHOTS / f"{f}.png")


if __name__ == "__main__":
    main()
