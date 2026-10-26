#include "settings_store.h"

#include <string.h>

#include "board.h"
#include "config.h"
#include "stm32f4xx_hal.h"

#define REGION ((const uint8_t *)SETTINGS_FLASH_ADDR)

/* Ende der Firmware im Flash (Linker-Symbole) */
extern uint32_t _sidata, _sdata, _edata;

static uint32_t firmware_end(void)
{
    return (uint32_t)&_sidata + ((uint32_t)&_edata - (uint32_t)&_sdata);
}

bool settings_store_load(void)
{
    int next;
    if (firmware_end() > SETTINGS_FLASH_ADDR) {
        settings_defaults(&g_set);
        return false;
    }
    return settings_scan(REGION, SETTINGS_FLASH_SIZE, &g_set, &next);
}

static void flash_wait(void)
{
    while (FLASH->SR & FLASH_SR_BSY) {
    }
}

static void flash_unlock(void)
{
    if (FLASH->CR & FLASH_CR_LOCK) {
        FLASH->KEYR = 0x45670123u;
        FLASH->KEYR = 0xCDEF89ABu;
    }
    FLASH->SR = FLASH_SR_EOP | FLASH_SR_OPERR | FLASH_SR_WRPERR | FLASH_SR_PGAERR |
                FLASH_SR_PGPERR | FLASH_SR_PGSERR;
}

static void flash_lock(void)
{
    FLASH->CR = FLASH_CR_LOCK;
    /* ART-Caches verwerfen, sonst koennen alte Daten gelesen werden */
    uint32_t acr = FLASH->ACR;
    FLASH->ACR = acr & ~(FLASH_ACR_DCEN | FLASH_ACR_ICEN);
    FLASH->ACR = (acr & ~(FLASH_ACR_DCEN | FLASH_ACR_ICEN)) | FLASH_ACR_DCRST | FLASH_ACR_ICRST;
    FLASH->ACR = acr;
}

static bool flash_erase_sector(void)
{
    board_watchdog_long(1);                    /* Loeschen: typ. 1 s, max. 2 s */
    flash_unlock();
    flash_wait();
    FLASH->CR = FLASH_CR_PSIZE_1 | FLASH_CR_SER | (SETTINGS_FLASH_SECTOR << FLASH_CR_SNB_Pos);
    FLASH->CR |= FLASH_CR_STRT;
    flash_wait();                              /* CPU steht hier (Single Bank) */
    bool ok = (FLASH->SR & (FLASH_SR_OPERR | FLASH_SR_WRPERR | FLASH_SR_PGSERR)) == 0u;
    flash_lock();
    board_watchdog_long(0);
    return ok;
}

static bool flash_program(uint32_t addr, const uint8_t *data, size_t len)
{
    flash_unlock();
    flash_wait();
    FLASH->CR = FLASH_CR_PSIZE_1 | FLASH_CR_PG;  /* 32 bit je Schreibvorgang */
    for (size_t i = 0; i < len; i += 4u) {
        uint32_t w;
        memcpy(&w, data + i, 4);
        *(volatile uint32_t *)(addr + i) = w;
        flash_wait();
    }
    bool ok = (FLASH->SR & (FLASH_SR_WRPERR | FLASH_SR_PGAERR | FLASH_SR_PGPERR | FLASH_SR_PGSERR)) == 0u;
    flash_lock();
    return ok;
}

const char *settings_store_save(const settings_t *s, bool busy)
{
    if (busy)
        return "messung laeuft";               /* CPU-Stillstand nie waehrend einer Messung */
    if (firmware_end() > SETTINGS_FLASH_ADDR)
        return "firmware zu gross";

    settings_t tmp;
    int slot;
    settings_scan(REGION, SETTINGS_FLASH_SIZE, &tmp, &slot);
    if (slot < 0) {
        if (!flash_erase_sector())
            return "loeschen fehlgeschlagen";
        slot = 0;
    }

    static uint8_t rec[SETTINGS_SLOT];
    settings_serialize(s, rec);
    /* nur den belegten Teil schreiben, Rest bleibt geloescht (0xFF) */
    size_t used = 8u + 4u * S_COUNT + 4u;
    used = (used + 3u) & ~3u;
    uint32_t addr = SETTINGS_FLASH_ADDR + (uint32_t)slot * SETTINGS_SLOT;
    if (!flash_program(addr, rec, used) || memcmp((const void *)addr, rec, SETTINGS_SLOT) != 0)
        return "schreiben fehlgeschlagen";
    return NULL;
}
