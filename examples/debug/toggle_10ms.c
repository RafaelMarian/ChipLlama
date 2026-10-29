#include <xc.h>
// CONFIG3
#pragma config WPFP = WPFP511     // Write-protect boundary (unused, WPDIS)
#pragma config WPDIS = WPDIS      // Segmented code protection disabled
#pragma config WPCFG = WPCFGDIS   // Config page not protected
#pragma config WPEND = WPENDMEM   // (only with WPDIS=WPEN)
// CONFIG2
#pragma config POSCMOD = NONE     // Primary oscillator DISABLED (no crystal on the board)
#pragma config DISUVREG = OFF     // Internal USB 3.3V regulator disabled
#pragma config IOL1WAY = OFF      // PPS registers can be rewritten
#pragma config OSCIOFNC = ON      // OSCO/RC15 pin is general I/O (not CLKO)
#pragma config FCKSM = CSDCMD     // Clock switching and fail-safe monitor disabled
#pragma config FNOSC = FRCPLL     // FRC + PLL
#pragma config PLL_96MHZ = ON     // 96 MHz PLL enabled
#pragma config PLLDIV = NODIV     // PLL prescaler -> PLL input must be 4 MHz
#pragma config IESO = OFF         // Two-speed start-up disabled
// CONFIG1
#pragma config WDTPS = PS32768    // WDT postscaler (WDT off)
#pragma config FWPSA = PR128      // WDT prescaler (WDT off)
#pragma config WINDIS = OFF       // Standard WDT
#pragma config FWDTEN = OFF       // Watchdog timer disabled
#pragma config ICS = PGx2         // Debug/programming on PGEC2/PGED2 (must match YOUR board wiring)
#pragma config GWRP = OFF         // No program-memory write protect
#pragma config GCP = OFF          // No code protection
#pragma config JTAGEN = OFF       // JTAG disabled (frees RA0/RA1/RA4/RA5)
#define FOSC 32000000UL
#define FCY  (FOSC / 2UL)
#define PRESCALE 8UL
#define PERIOD_MS 10UL
/* ticks = FCY/1000*PERIOD_MS/PRESCALE = 20000 ; PR = ticks-1 */
void timer1_init(void)
{
    T1CON = 0;                       /* stop, internal clock */
    TMR1 = 0;
    PR1 = (FCY / 1000UL * PERIOD_MS / PRESCALE) - 1UL;
    T1CONbits.TCKPS = 1;             /* 1:8 */
    IPC0bits.T1IP = 5;
    IFS0bits.T1IF = 0;
    IEC0bits.T1IE = 1;
    T1CONbits.TON = 1;
}
void __attribute__((interrupt, auto_psv)) _T1Interrupt(void)
{
    LATAbits.LATA0 ^= 1;             /* toggle RA0 */
    IFS0bits.T1IF = 0;
}
int main(void) { CLKDIVbits.RCDIV = 1; CLKDIVbits.CPDIV = 0; TRISAbits.TRISA0 = 0; timer1_init(); while (1) { } }
