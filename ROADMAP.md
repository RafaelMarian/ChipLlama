# ChipLlama roadmap

## Done (hackathon, Oct 27 - Nov 1 2026)
- **M1** Generate → verify (register checker, C emulator, XC16 compile) → self-correct; reverse debug from screenshots/dumps. Verified live Nov 1 (Timer1, first-try pass + fault-injection self-correct, real XC16 v2.10).
- **M2** Blocks tab: Blockly → deterministic, verified C; "polish with Muse".
- **M3** Audit: static rules for existing code / MPLAB X projects, datasheet pages, patched copy (`chipllama audit --fix`).
- **M5** Premium UI: sidebar shell, dark design tokens, vendored fonts, empty states, toasts, Ctrl+Enter, responsive ≥1100 px.
- **M4** Pin planner (PPS/fixed pins/JTAG/ICS conflicts + `pins_init()`), explain-code mode, rough power estimate.
- **M6** Circuit tab: netlist, MCU pin-function assignment, ERC with datasheet pages, HAL/board/drivers project generation + MPLAB X export.
- **M7** Schematic: KiCad-style auto-wire, 50-mil-grid geometry/router, MCU power checklist, KiCad layout export.
- **M8** MCU family plugins: Arduino AVR (Uno/Nano/Mega), STM32 (Blue Pill, Nucleo-F411RE, F030), ESP32 (DevKitC, C3), across Generate, Circuit, Blocks, Audit, Doctor and Settings. PIC stays primary.
- **M9** Auto re-wire: remap a circuit onto a new device, rebuild MCU support circuitry, re-place and re-route.

## Next
- **FPGA (roadmap only)**: iCE40 / ECP5 via yosys + nextpnr, plus Gowin. Verilog generation checked by a Verilator lint and a simulation testbench, with pin constraints (PCF/LPF) from the Circuit tab. Not started.
- **Families v2**: RP2040, nRF52, SAMD21. Bare-metal STM32 HAL for ADC DMA and USB CDC. ESP-IDF mode. Family-aware graphical pin planner.
- **Power numbers from the datasheet**: replace the typical-order constants in `power.py` with DS39897C sec. 26 IDD/IIDLE/IPD table rows (by Fcy, VDD, temperature) and per-module currents.
- **Pin planner v2**: full 100-pin package map (graphical), I2C alternate pins, CN/INTx inputs, input-capture/gate inputs, import from existing code (reverse the PPS writes).
- **More devices**: dsPIC33 / PIC24 families via MPLAB pack EDC files end-to-end (checker + audit + pins), then PIC32 with XC32.
- **Audit**: more rules (DMA, RTCC unlock, CRC), project-wide data-flow, SARIF output for CI, VS Code / MPLAB X extension.
- **Hardware-in-the-loop**: optional PICkit 5 flash + UART capture to confirm behaviour (flashing stays off by default and requires explicit confirmation).
- **Explain mode**: interactive per-line hover in the editor, export as Markdown.
- **Packaging**: signed Windows installer, offline datasheet page cache.

## Not planned
- Sending code or keys anywhere except the model backend you configure. Keys stay in your local `.env` / `settings.json`.
