#include <xc.h>
#include "uart.h"

void uart_init(void)
{
    U1MODE = 0;
    U1MODEbits.BRGH = 1;
    U1BRG = 34;                     /* 115200 @ Fcy 16 MHz */
    U1MODEbits.UARTEN = 1;
    U1STAbits.UTXEN = 1;
}

void uart_putc(char c)
{
    while (U1STAbits.UTXBF);
    U1TXREG = c;
}
