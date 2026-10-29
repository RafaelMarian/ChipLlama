/* Buggy blinky for the ChipLlama audit demo - DO NOT FLASH AS-IS.
 * Intended: LED on RA0 blinks every 500 ms from Timer1, LED2 on RA1 shows a heartbeat. */
#include <xc.h>

#pragma config FWDTEN = ON      // Watchdog timer
#pragma config JTAGEN = ON      // JTAG port
#pragma config ICS = PGx2       // Debug pins
#pragma config FNOSC = FRCPLL   // FRC + PLL
#pragma config PLL_96MHZ = ON
#pragma config PLLDIV = NODIV
#pragma config POSCMOD = NONE

#define FCY 8000000UL
#include <libpic30.h>

unsigned int ticks = 0;

void __attribute__((interrupt, auto_psv)) _T1interrupt(void)
{
    ticks++;
    PORTAbits.RA0 ^= 1;
    IFS0bits.T1IF = 0;
}

int main(void)
{
    CLKDIVbits.RCDIV = 1;
    CLKDIVbits.CPDIV = 0;
    TRISAbits.TRISA0 = 0;
    T1CON = 0;
    T1CONbits.TCKPS = 3;
    PR1 = 31249;
    IFS0bits.T1IF = 0;
    IEC0bits.T1IE = 1;
    T1CONbits.TON = 1;
    while (1) {
        if (ticks > 10) {
            ticks = 0;
            LATAbits.LATA1 ^= 1;
        }
        __delay_ms(10);
    }
}
