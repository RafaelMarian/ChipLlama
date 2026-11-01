/* MPLAB X style project for the ChipLlama audit demo - DO NOT FLASH AS-IS.
 * Intended: UART1 115200 on RP10 (TX) / RP17 (RX), echo received bytes,
 * print a button state (RB3) and sample AN5 with the ADC interrupt. */
#include <xc.h>
#include "uart.h"

#pragma config FWDTEN = OFF
#pragma config JTAGEN = OFF
#pragma config ICS = PGx1
#pragma config FNOSC = FRCPLL
#pragma config PLL_96MHZ = ON
#pragma config PLLDIV = NODIV
#pragma config POSCMOD = NONE

#include <libpic30.h>

volatile unsigned int last_sample;

void __attribute__((interrupt)) _U1RXInterrupt(void)
{
    char c = U1RXREG;
    uart_putc(c);
}

int main(void)
{
    CLKDIVbits.RCDIV = 1;
    CLKDIVbits.CPDIV = 0;
    uart_init();
    TRISBbits.TRISB3 = 1;           /* button */
    AD1PCFGL = 0xFFDF;              /* AN5 analog */
    AD1PCFGLbits.PCFG3 = 0;
    AD1CON1 = 0x80E4;               /* auto-sample, auto-convert */
    AD1CHS = 5;
    IFS0bits.AD1IF = 0;
    IEC0bits.AD1IE = 1;
    IEC0bits.U1RXIE = 1;
    while (1) {
        uart_putc(PORTBbits.RB3 ? '1' : '0');
        __delay_ms(100);
    }
}
