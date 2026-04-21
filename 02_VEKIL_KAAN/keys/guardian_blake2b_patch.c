/* kernel/guardian_blake2b_patch.c
 * FAZ 18: make_binding() → CRC32 zinciri YERİNE BLAKE2b-256
 *
 * Bu dosya guardian.c içindeki make_binding() ve guardian_vpath_sign()
 * fonksiyonlarını REPLACE eder.
 *
 * NEDEN:
 *   Eski: binding[32] = 8 × CRC32_chain  (kriptografik değil)
 *   Yeni: binding[32] = BLAKE2b-256(input_vector)  (Python ile özdeş)
 *
 * Python eşdeğeri (SDCK_UAGL_UNIFIED_v2.py, satır 334):
 *   binding = hashlib.blake2b(binding_input, digest_size=32).digest()
 *
 * binding_input yapısı (Python'daki ile birebir aynı):
 *   [0:1]   version
 *   [1:5]   counter (LE)
 *   [5:13]  timestamp (LE)
 *   [13:17] g1 (float32 LE)
 *   [17:21] g2 (float32 LE)
 *   [21:25] g3 (float32 LE)
 *   [25:29] g4 (float32 LE)
 *   [29:30] tessa_id
 *   [30:32] kappa_q (LE)
 *   [32:48] chaos[4] (4 × float32 LE)
 *   [48:49] from_v
 *   [49:50] to_v
 *   [50:54] domain ('KRAL')
 *   [54:58] flags (LE)
 *   [58:62] prev_crc (LE)
 *   [62:70] chaos_seed (LE)
 *   Total: 70 byte
 *
 * 𐰚𐰺𐰞 · FAZ 18 · □ + ◇ = 1OF1
 */

#include "guardian.h"
#include "blake2b.h"
#include <stdint.h>

/* ── Yardımcı: LE write ─────────────────────────────────────── */
static inline void _wr32(uint8_t *p, uint32_t v) {
    p[0]=(uint8_t)v; p[1]=(uint8_t)(v>>8);
    p[2]=(uint8_t)(v>>16); p[3]=(uint8_t)(v>>24);
}
static inline void _wr64(uint8_t *p, uint64_t v) {
    p[0]=(uint8_t)v;      p[1]=(uint8_t)(v>>8);
    p[2]=(uint8_t)(v>>16); p[3]=(uint8_t)(v>>24);
    p[4]=(uint8_t)(v>>32); p[5]=(uint8_t)(v>>40);
    p[6]=(uint8_t)(v>>48); p[7]=(uint8_t)(v>>56);
}
static inline void _wrf32(uint8_t *p, float f) {
    union { float f; uint32_t u; } x; x.f = f; _wr32(p, x.u);
}

/* ── make_binding_blake2b: Python'un hashlib.blake2b ile özdeş ─ */
static void make_binding_blake2b(uint8_t out[32], const GuardianWire *w) {
    /*
     * binding_input: 70 byte — Python SDCK_UAGL_UNIFIED_v2.py ile aynı sıra
     */
    uint8_t inp[70];
    uint8_t *p = inp;

    p[0] = w->version;          p++;                /* 1  */
    _wr32(p, w->counter);       p += 4;             /* 4  */
    _wr64(p, w->timestamp);     p += 8;             /* 8  */
    _wrf32(p, w->g1);           p += 4;             /* 4  */
    _wrf32(p, w->g2);           p += 4;             /* 4  */
    _wrf32(p, w->g3);           p += 4;             /* 4  */
    _wrf32(p, w->g4);           p += 4;             /* 4  */
    p[0] = w->tessa_id;         p++;                /* 1  */
    p[0] = (uint8_t)(w->kappa_q);
    p[1] = (uint8_t)(w->kappa_q >> 8); p += 2;     /* 2  */
    _wrf32(p, w->chaos[0]);     p += 4;             /* 4  */
    _wrf32(p, w->chaos[1]);     p += 4;             /* 4  */
    _wrf32(p, w->chaos[2]);     p += 4;             /* 4  */
    _wrf32(p, w->chaos[3]);     p += 4;             /* 4  */
    p[0] = w->from_v;           p++;                /* 1  */
    p[0] = w->to_v;             p++;                /* 1  */
    p[0]=w->domain[0]; p[1]=w->domain[1];
    p[2]=w->domain[2]; p[3]=w->domain[3]; p += 4;  /* 4  */
    _wr32(p, w->flags);         p += 4;             /* 4  */
    _wr32(p, w->prev_crc);      p += 4;             /* 4  */
    _wr64(p, w->chaos_seed);    p += 8;             /* 8  */
    /* Toplam: 1+4+8+4+4+4+4+1+2+4+4+4+4+1+1+4+4+4+8 = 70 */

    /* BLAKE2b-256: Python hashlib.blake2b(inp, digest_size=32) ile özdeş */
    blake2b_256(out, inp, (size_t)(p - inp));
}

/* ── guardian_vpath_sign — BLAKE2b binding ile ──────────────── */
GuardianWire guardian_vpath_sign_b2(
    int from_v, int to_v,
    double g1, double g2, double g3, double g4,
    TessaResult tessa,
    int kral_frame_active)
{
    extern uint32_t _counter, _prev_crc;
    extern uint64_t _tick;

    GuardianWire w = {0};

    w.version   = GUARDIAN_VERSION;
    w.counter   = _counter++;
    w.timestamp = ++_tick;
    w.g1 = (float)g1;  w.g2 = (float)g2;
    w.g3 = (float)g3;  w.g4 = (float)g4;
    w.tessa_id  = (uint8_t)(tessa.class_id & 0xFF);
    w.kappa_q   = (uint16_t)(tessa.kappa * 65535.0);
    w.chaos_seed = chaos_raw();
    for (int i = 0; i < 4; i++) w.chaos[i] = chaos_next_f();
    w.from_v    = (uint8_t)from_v;
    w.to_v      = (uint8_t)to_v;
    w.domain[0]='K'; w.domain[1]='R';
    w.domain[2]='A'; w.domain[3]='L';
    w.flags     = (uint32_t)(kral_frame_active ? 1 : 0)
                | (uint32_t)((to_v == 10)       ? 2 : 0);
    w.prev_crc  = _prev_crc;

    /* BLAKE2b binding — Python ile özdeş */
    make_binding_blake2b(w.binding, &w);

    /* CRC32 bütünlük — son 4 byte (değişmedi) */
    w.crc32 = crc32_compute((const uint8_t*)&w, 108);
    _prev_crc = w.crc32;

    g_guardian_status.total_sigs++;
    g_guardian_status.last_counter    = w.counter;
    g_guardian_status.last_chaos_seed = w.chaos_seed;
    return w;
}

/* ── guardian_vpath_verify — BLAKE2b ile doğrulama ─────────── */
GuardianVerifyResult guardian_vpath_verify_b2(
    const GuardianWire *wire,
    const GuardianWire *prev)
{
    GuardianVerifyResult r = {0};

    /* 1. Anti-replay */
    if (prev) {
        r.anti_replay_ok = (wire->counter > prev->counter) ? 1 : 0;
    } else {
        r.anti_replay_ok = 1;
    }

    /* 2. CRC32 bütünlük */
    uint32_t expected_crc = crc32_compute((const uint8_t*)wire, 108);
    r.crc_ok = (expected_crc == wire->crc32) ? 1 : 0;

    /* 3. BLAKE2b binding doğrulama */
    uint8_t expected_binding[32];
    make_binding_blake2b(expected_binding, wire);
    int binding_match = 1;
    for (int i = 0; i < 32; i++) {
        if (expected_binding[i] != wire->binding[i]) {
            binding_match = 0;
            break;
        }
    }
    r.binding_ok = binding_match;

    /* 4. TESSA aralık */
    r.tessa_ok = (wire->tessa_id <= 10) ? 1 : 0;

    /* 5. Kappa tutarlılık */
    r.kappa_ok = (wire->kappa_q > 0) ? 1 : 0;

    r.valid = r.crc_ok && r.binding_ok && r.tessa_ok && r.anti_replay_ok;

    if      (!r.crc_ok)         { r.reason[0]='C'; r.reason[1]='R'; r.reason[2]='C'; r.reason[3]=0; }
    else if (!r.binding_ok)     { r.reason[0]='B'; r.reason[1]='I'; r.reason[2]='N'; r.reason[3]='D'; r.reason[4]=0; }
    else if (!r.anti_replay_ok) { r.reason[0]='R'; r.reason[1]='E'; r.reason[2]='P'; r.reason[3]='L'; r.reason[4]='Y'; r.reason[5]=0; }
    else if (!r.tessa_ok)       { r.reason[0]='T'; r.reason[1]='E'; r.reason[2]='S'; r.reason[3]='S'; r.reason[4]='A'; r.reason[5]=0; }
    else                        { r.reason[0]='O'; r.reason[1]='K'; r.reason[2]=0; }

    if (r.valid) g_guardian_status.verify_ok++;
    else         g_guardian_status.verify_fail++;

    return r;
}
