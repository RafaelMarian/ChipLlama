"""M9.2 UI check: click 'Auto re-wire', Shift+R, Ctrl+Z, and device switches; every result must have 0 drawing problems.
Usage: CL_URL=http://127.0.0.1:8000 CL_SHOTS=/tmp/shots python scripts/check_rewire_ui.py [shots]"""
import asyncio, json, os, sys
from playwright.async_api import async_playwright
B = os.environ.get("CL_URL", "http://127.0.0.1:8777"); O = os.environ.get("CL_SHOTS", "/workspace/ui_shots/")
CASES = [("led_button", ["ARDUINO-MEGA2560", "ESP32-DEVKITC", "STM32-BLUEPILL", "PIC18F45K22"]),
         ("i2c_logger", ["ARDUINO-UNO", "NUCLEO-F411RE"]), ("arduino_uno_uart", ["PIC24FJ256GB110", "ESP32-C3-DEVKITM"])]


async def toast(pg, pat):
    await pg.wait_for_function(f"[...document.querySelectorAll('#toasts .toast')].some(t=>/{pat}/.test(t.textContent))", timeout=60000)
    return await pg.evaluate(f"[...document.querySelectorAll('#toasts .toast')].map(t=>t.textContent).filter(t=>/{pat}/.test(t)).pop()")


async def clear(pg):
    await pg.evaluate("document.querySelectorAll('#toasts .toast').forEach(t=>t.remove())")


async def main(shots):
    fails = []
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=os.environ.get("CL_CHROME", "/usr/bin/google-chrome"))
        pg = await b.new_page(viewport={"width": 1600, "height": 1000}); pg.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
        await pg.goto(B); await pg.evaluate("localStorage.removeItem('cl_circuit')"); await pg.reload(); await pg.wait_for_timeout(1500)
        await pg.evaluate("tab('cir')"); await pg.wait_for_timeout(1200)
        for ex, devs in CASES:
            await pg.evaluate(f"Circuit.loadEx('{ex}')"); await pg.wait_for_timeout(1200)
            before = await pg.evaluate("JSON.stringify(Circuit.state())")
            await clear(pg); await pg.click("#crewire")
            t = await toast(pg, "Re-wired"); print(ex, "button:", t)
            a = await pg.evaluate("Circuit.audit()")
            if a["problems"]: fails.append((ex, "button", a["problems"][:2]))
            if shots: await pg.locator('#cwrap').screenshot(path=f"{O}m92_{ex}_rewired.png")
            await pg.keyboard.press("Control+z"); await pg.wait_for_timeout(300)
            if await pg.evaluate("JSON.stringify(Circuit.state())") != before: fails.append((ex, "undo did not restore"))
            for d in devs:
                await clear(pg); await pg.select_option("#cdev", d)
                t = await toast(pg, f"Re-wired for {d}"); print(ex, "->", d, ":", t)
                await pg.wait_for_timeout(400)
                a = await pg.evaluate("Circuit.audit()")
                if a["problems"]: fails.append((ex, d, a["problems"][:2]))
                if await pg.evaluate("Circuit.state().device") != d: fails.append((ex, d, "device not set"))
                if shots: await pg.evaluate("Circuit.fit()"); await pg.wait_for_timeout(200); await pg.locator('#cwrap').screenshot(path=f"{O}m92_{ex}_to_{d}.png")
            await clear(pg); await pg.keyboard.press("Shift+R"); await toast(pg, "Re-wired:")
        # validator badge: drag a wire through the MCU body -> badge + Fix
        await pg.evaluate("Circuit.loadEx('led_button')"); await pg.wait_for_timeout(1200)
        await pg.evaluate("""(()=>{const D=Circuit.state();const m=D.parts.find(p=>p.type==='mcu');
          const w=D.wires.find(w=>w.pts&&(w.a.startsWith(m.id+'.')||w.b.startsWith(m.id+'.')));
          w.pts=[{x:m.x+60,y:m.y-40},{x:m.x+60,y:m.y+400}]})()""")
        await pg.evaluate("Circuit.annotate()")  # any edit -> changed() -> validator
        try:
            await pg.wait_for_selector("#cgeo:not(.hide)", timeout=8000)
            if shots: await pg.locator('#cwrap').screenshot(path=f"{O}m92_badge.png")
            await pg.click("#cgeo button"); await pg.wait_for_selector("#cgeo.hide", state="attached", timeout=20000)
            print("badge: shown and fixed")
        except Exception as e:
            fails.append(("badge", repr(e)[:200]))
        await b.close()
    print("FAILS", json.dumps(fails)[:3000]); sys.exit(1 if fails else 0)

asyncio.run(main(len(sys.argv) > 1))
