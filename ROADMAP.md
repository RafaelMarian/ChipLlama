# ChipLlama roadmap

## Done (hackathon, Oct 27-29 2026)
- **M1** Generate → verify (register checker, C emulator, XC16 compile) → self-correct; reverse debug from screenshots/dumps.
- **M2** Blocks tab: Blockly → deterministic, verified C; "polish with Muse".
- **M3** Audit: static rules for existing code / MPLAB X projects, datasheet pages, patched copy (`chipllama audit --fix`).
- **M5** Premium UI: sidebar shell, dark design tokens, vendored fonts, empty states, toasts, Ctrl+Enter, responsive ≥1100 px.
- **M4** Pin planner (PPS/fixed pins/JTAG/ICS conflicts + `pins_init()`), explain-code mode, rough power estimate.

## Next
- **Power numbers from the datasheet**: replace the typical-order constants in `power.py` with DS39897C sec. 26 IDD/IIDLE/IPD table rows (by Fcy, VDD, temperature) and per-module currents.
- **Pin planner v2**: full 100-pin package map (graphical), I2C alternate pins, CN/INTx inputs, input-capture/gate inputs, import from existing code (reverse the PPS writes).
- **More devices**: dsPIC33 / PIC24 families via MPLAB pack EDC files end-to-end (checker + audit + pins), then PIC32 with XC32.
- **Audit**: more rules (DMA, RTCC unlock, CRC), project-wide data-flow, SARIF output for CI, VS Code / MPLAB X extension.
- **Hardware-in-the-loop**: optional PICkit 5 flash + UART capture to confirm behaviour (flashing stays off by default and requires explicit confirmation).
- **Explain mode**: interactive per-line hover in the editor, export as Markdown.
- **Packaging**: signed Windows installer, offline datasheet page cache.

## Not planned
- Sending code or keys anywhere except the model backend you configure. Keys stay in your local `.env` / `settings.json`.
