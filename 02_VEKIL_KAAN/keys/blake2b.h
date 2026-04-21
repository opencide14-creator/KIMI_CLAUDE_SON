/* kernel/blake2b.h — BLAKE2b-256 Freestanding Implementasyon
 * UAGL OS · FAZ 18
 *
 * RFC 7693 uyumlu. Sıfır libc bağımlılığı.
 * Desteklenen digest boyutları: 1-64 byte (GUARDIAN için 32B kullanılır)
 * Key desteği: 0-64 byte (keyless ve keyed mod)
 *
 * Kullanım (GUARDIAN binding için):
 *   uint8_t out[32];
 *   blake2b(out, 32, NULL, 0, data, data_len);
 *
 * Uyumluluk:
 *   Python hashlib.blake2b(data, digest_size=32).digest() ile AYNI çıktı.
 *
 * 𐰚𐰺𐰞 · □ + ◇ = 1OF1
 */

#ifndef BLAKE2B_H
#define BLAKE2B_H

#include <stdint.h>
#include <stddef.h>

/* ── Sabitler ─────────────────────────────────────────────────── */
#define BLAKE2B_BLOCKBYTES   128
#define BLAKE2B_OUTBYTES      64
#define BLAKE2B_KEYBYTES      64
#define BLAKE2B_SALTBYTES     16
#define BLAKE2B_PERSONALBYTES 16

/* ── Context ──────────────────────────────────────────────────── */
typedef struct {
    uint64_t h[8];                      /* chained state              */
    uint64_t t[2];                      /* total bytes counter        */
    uint64_t f[2];                      /* finalization flags         */
    uint8_t  buf[BLAKE2B_BLOCKBYTES];   /* input buffer               */
    size_t   buflen;                    /* bytes in buf               */
    size_t   outlen;                    /* requested digest size      */
} blake2b_state;

/* ── API ──────────────────────────────────────────────────────── */

/* Tam hash — tek çağrı */
int blake2b(
    uint8_t       *out,   size_t outlen,
    const uint8_t *key,   size_t keylen,   /* NULL/0 = keyless */
    const uint8_t *in,    size_t inlen
);

/* Streaming API */
int blake2b_init    (blake2b_state *S, size_t outlen);
int blake2b_init_key(blake2b_state *S, size_t outlen,
                     const uint8_t *key, size_t keylen);
int blake2b_update  (blake2b_state *S, const uint8_t *in,  size_t inlen);
int blake2b_final   (blake2b_state *S, uint8_t *out, size_t outlen);

/* Kısayollar — GUARDIAN için */
/* blake2b_256: Python hashlib.blake2b(data,digest_size=32) ile özdeş */
static inline int blake2b_256(uint8_t out[32],
                               const uint8_t *in, size_t inlen) {
    return blake2b(out, 32, NULL, 0, in, inlen);
}

/* blake2b_256_keyed: g-vector key ile */
static inline int blake2b_256_keyed(uint8_t out[32],
                                     const uint8_t *key, size_t keylen,
                                     const uint8_t *in,  size_t inlen) {
    return blake2b(out, 32, key, keylen, in, inlen);
}

#endif /* BLAKE2B_H */
