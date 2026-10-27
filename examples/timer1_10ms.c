#include <xc.h>
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
    IFS0bits.T1IF = 0;
}
int main(void) { timer1_init(); while (1) { } }
