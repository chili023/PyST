/*
 * PC-Protokoll.
 *
 * 1) Arduino-Mega-Protokoll (LabVIEW WildBugChilGru 3.x, aktuelle Version):
 *    Befehle: einzelne Zeichen, optional mit '\n'
 *      e  -> "T;P;H\r\n"   Temperatur degC (1 Nachkommast.), Druck Pa (0), Feuchte % (1)
 *      m  -> Messung starten (siehe M_TOGGLES in config.h)
 *      s  -> Messung stoppen (Erweiterung)
 *      v  -> Versions- und Konfigurationsinfo
 *    Telegramm: "Zyklus;Messfrequenz;f_Zuend;f_Rolle;EGT1;AFR;EGT2\n"
 *
 * 2) Protokoll der privaten STM-LabVIEW 0.2.0 (wird automatisch erkannt,
 *    sobald genau 8 Bytes am Stueck kommen: "tttttttt", "eeeeeeee",
 *    "lllllllX", "mRabcdef"):
 *    Telegramm: 20 Byte binaer, 10 x uint16 little endian.
 */
#ifndef PROTOCOL_H
#define PROTOCOL_H

#include "capture.h"

void protocol_init(void);
void protocol_poll(void);                          /* Befehle verarbeiten */
void protocol_on_frame(const capture_frame_t *f);  /* je Telegramm-Intervall */
int protocol_streaming(void);

#endif /* PROTOCOL_H */
