/* kernel/blake2b.c — BLAKE2b-256 Tam Implementasyon (RFC 7693)
 * UAGL OS · FAZ 18
 *
 * Kaynak: RFC 7693 referans implementasyonundan türetildi.
 * Freestanding — libc/memcpy/memset YOK, tümü inline.
 *
 * Test vektörü (RFC 7693 Appendix A):
 *   blake2b("", 64, NULL, 0, NULL, 0) =
 *   786a02f742015903c6c6fd852552d272912f4740e15847618a86e217f71f5419...
 *
 * Python eşdeğeri:
 *   import hashlib
 *   hashlib.blake2b(b"data", digest_size=32).digest()
 *
 * 𐰚𐰺𐰞 · □ + ◇ = 1OF1
 */

#include "blake2b.h"

/* ── Freestanding helpers (libc yok) ─────────────────────────── */
static void _b2_memset(void *dst, int c, size_t n) {
    uint8_t *p = (uint8_t *)dst;
    while (n--) *p++ = (uint8_t)c;
}

static void _b2_memcpy(void *dst, const void *src, size_t n) {
    const uint8_t *s = (const uint8_t *)src;
    uint8_t       *d = (uint8_t *)dst;
    while (n--) *d++ = *s++;
}

/* ── Little-endian 64-bit load ───────────────────────────────── */
static inline uint64_t load64(const uint8_t *p) {
    return ((uint64_t)p[0]      ) | ((uint64_t)p[1] <<  8) |
           ((uint64_t)p[2] << 16) | ((uint64_t)p[3] << 24) |
           ((uint64_t)p[4] << 32) | ((uint64_t)p[5] << 40) |
           ((uint64_t)p[6] << 48) | ((uint64_t)p[7] << 56);
}

static inline void store64(uint8_t *p, uint64_t v) {
    p[0] = (uint8_t)(v      ); p[1] = (uint8_t)(v >>  8);
    p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
    p[4] = (uint8_t)(v >> 32); p[5] = (uint8_t)(v >> 40);
    p[6] = (uint8_t)(v >> 48); p[7] = (uint8_t)(v >> 56);
}

static inline uint64_t rotr64(uint64_t x, int n) {
    return (x >> n) | (x << (64 - n));
}

/* ── IV — SHA-512 başlangıç sabitleri ────────────────────────── */
static const uint64_t BLAKE2B_IV[8] = {
    0x6A09E667F3BCC908ULL, 0xBB67AE8584CAA73BULL,
    0x3C6EF372FE94F82BULL, 0xA54FF53A5F1D36F1ULL,
    0x510E527FADE682D1ULL, 0x9B05688C2B3E6C1FULL,
    0x1F83D9ABFB41BD6BULL, 0x5BE0CD19137E2179ULL
};

/* ── Sigma permütasyon tablosu ───────────────────────────────── */
static const uint8_t BLAKE2B_SIGMA[12][16] = {
    { 0,  1,  2,  3,  4,  5,  6,  7,  8,  9, 10, 11, 12, 13, 14, 15},
    {14, 10,  4,  8,  9, 15, 13,  6,  1, 12,  0,  2, 11,  7,  5,  3},
    {11,  8, 12,  0,  5,  2, 15, 13, 10, 14,  3,  6,  7,  1,  9,  4},
    { 7,  9,  3,  1, 13, 12, 11, 14,  2,  6,  5, 10,  4,  0, 15,  8},
    { 9,  0,  5,  7,  2,  4, 10, 15, 14,  1, 11, 12,  6,  8,  3, 13},
    { 2, 12,  6, 10,  0, 11,  8,  3,  4, 13,  7,  5, 15, 14,  1,  9},
    {12,  5,  1, 15, 14, 13,  4, 10,  0,  7,  6,  3,  9,  2,  8, 11},
    {13, 11,  7, 14, 12,  1,  3,  9,  5,  0, 15,  4,  8,  6,  2, 10},
    { 6, 15, 14,  9, 11,  3,  0,  8, 12,  2, 13,  7,  1,  4, 10,  5},
    {10,  2,  8,  4,  7,  6,  1,  5, 15, 11,  9, 14,  3, 12, 13,  0},
    { 0,  1,  2,  3,  4,  5,  6,  7,  8,  9, 10, 11, 12, 13, 14, 15},
    {14, 10,  4,  8,  9, 15, 13,  6,  1, 12,  0,  2, 11,  7,  5,  3},
};

/* ── G fonksiyonu ────────────────────────────────────────────── */
#define G(r, i, a, b, c, d)                       \
    do {                                           \
        a = a + b + m[BLAKE2B_SIGMA[r][2*i+0]];   \
        d = rotr64(d ^ a, 32);                     \
        c = c + d;                                 \
        b = rotr64(b ^ c, 24);                     \
        a = a + b + m[BLAKE2B_SIGMA[r][2*i+1]];   \
        d = rotr64(d ^ a, 16);                     \
        c = c + d;                                 \
        b = rotr64(b ^ c, 63);                     \
    } while(0)

/* ── Compress (tek blok işleme) ──────────────────────────────── */
static void blake2b_compress(blake2b_state *S, const uint8_t *block) {
    uint64_t m[16];
    uint64_t v[16];

    for (int i = 0; i < 16; i++)
        m[i] = load64(block + i * 8);

    for (int i = 0; i < 8; i++)
        v[i] = S->h[i];

    v[ 8] = BLAKE2B_IV[0];
    v[ 9] = BLAKE2B_IV[1];
    v[10] = BLAKE2B_IV[2];
    v[11] = BLAKE2B_IV[3];
    v[12] = BLAKE2B_IV[4] ^ S->t[0];
    v[13] = BLAKE2B_IV[5] ^ S->t[1];
    v[14] = BLAKE2B_IV[6] ^ S->f[0];
    v[15] = BLAKE2B_IV[7] ^ S->f[1];

    /* 12 round */
    for (int r = 0; r < 12; r++) {
        G(r, 0, v[ 0], v[ 4], v[ 8], v[12]);
        G(r, 1, v[ 1], v[ 5], v[ 9], v[13]);
        G(r, 2, v[ 2], v[ 6], v[10], v[14]);
        G(r, 3, v[ 3], v[ 7], v[11], v[15]);
        G(r, 4, v[ 0], v[ 5], v[10], v[15]);
        G(r, 5, v[ 1], v[ 6], v[11], v[12]);
        G(r, 6, v[ 2], v[ 7], v[ 8], v[13]);
        G(r, 7, v[ 3], v[ 4], v[ 9], v[14]);
    }

    for (int i = 0; i < 8; i++)
        S->h[i] ^= v[i] ^ v[i + 8];
}

/* ── blake2b_init ─────────────────────────────────────────────── */
int blake2b_init(blake2b_state *S, size_t outlen) {
    if (outlen == 0 || outlen > BLAKE2B_OUTBYTES) return -1;

    _b2_memset(S, 0, sizeof(blake2b_state));

    for (int i = 0; i < 8; i++)
        S->h[i] = BLAKE2B_IV[i];

    /* Parameter block: fanout=1, depth=1, outlen=outlen */
    uint64_t p0 = (uint64_t)outlen         /* digest_length: byte 0 */
                | ((uint64_t)0    << 8)    /* key_length:    byte 1 = 0 */
                | ((uint64_t)1    << 16)   /* fanout:        byte 2 = 1 */
                | ((uint64_t)1    << 24);  /* depth:         byte 3 = 1 */
    S->h[0] ^= p0;

    S->outlen = outlen;
    return 0;
}

/* ── blake2b_init_key ─────────────────────────────────────────── */
int blake2b_init_key(blake2b_state *S, size_t outlen,
                     const uint8_t *key, size_t keylen) {
    if (outlen == 0 || outlen > BLAKE2B_OUTBYTES) return -1;
    if (keylen  == 0 || keylen  > BLAKE2B_KEYBYTES) return -1;

    _b2_memset(S, 0, sizeof(blake2b_state));

    for (int i = 0; i < 8; i++)
        S->h[i] = BLAKE2B_IV[i];

    uint64_t p0 = (uint64_t)outlen
                | ((uint64_t)keylen << 8)
                | ((uint64_t)1      << 16)
                | ((uint64_t)1      << 24);
    S->h[0] ^= p0;

    S->outlen = outlen;

    /* Anahtar bloğu — 128 byte'a pad edilmiş */
    uint8_t block[BLAKE2B_BLOCKBYTES];
    _b2_memset(block, 0, BLAKE2B_BLOCKBYTES);
    _b2_memcpy(block, key, keylen);
    blake2b_update(S, block, BLAKE2B_BLOCKBYTES);
    /* block içeriğini sıfırla (key temizliği) */
    _b2_memset(block, 0, BLAKE2B_BLOCKBYTES);

    return 0;
}

/* ── blake2b_update ───────────────────────────────────────────── */
int blake2b_update(blake2b_state *S, const uint8_t *in, size_t inlen) {
    if (inlen == 0) return 0;

    while (inlen > 0) {
        size_t left = S->buflen;
        size_t fill = BLAKE2B_BLOCKBYTES - left;

        if (inlen > fill) {
            /* Buffer'ı doldur ve compress et */
            _b2_memcpy(S->buf + left, in, fill);
            S->t[0] += BLAKE2B_BLOCKBYTES;
            if (S->t[0] < BLAKE2B_BLOCKBYTES) S->t[1]++;  /* carry */
            blake2b_compress(S, S->buf);
            S->buflen = 0;
            in     += fill;
            inlen  -= fill;
        } else {
            /* Buffer'ı kısmen doldur — son blok için */
            _b2_memcpy(S->buf + left, in, inlen);
            S->buflen += inlen;
            in        += inlen;
            inlen      = 0;
        }
    }
    return 0;
}

/* ── blake2b_final ────────────────────────────────────────────── */
int blake2b_final(blake2b_state *S, uint8_t *out, size_t outlen) {
    if (outlen == 0 || outlen > S->outlen) return -1;

    /* Son blok için finalization flag set et */
    S->f[0] = (uint64_t)-1;  /* 0xFFFFFFFFFFFFFFFF */

    /* Counter güncelle */
    S->t[0] += (uint64_t)S->buflen;
    if (S->t[0] < (uint64_t)S->buflen) S->t[1]++;

    /* Son buffer'ı sıfırlarla doldur ve compress et */
    _b2_memset(S->buf + S->buflen, 0, BLAKE2B_BLOCKBYTES - S->buflen);
    blake2b_compress(S, S->buf);

    /* Çıktıyı yaz (little-endian) */
    uint8_t full[BLAKE2B_OUTBYTES];
    for (int i = 0; i < 8; i++)
        store64(full + i * 8, S->h[i]);

    _b2_memcpy(out, full, outlen);
    _b2_memset(S, 0, sizeof(blake2b_state)); /* state temizliği */
    return 0;
}

/* ── blake2b — tek çağrı API ─────────────────────────────────── */
int blake2b(uint8_t       *out,   size_t outlen,
            const uint8_t *key,   size_t keylen,
            const uint8_t *in,    size_t inlen) {
    blake2b_state S;
    int ret;

    if (key && keylen > 0)
        ret = blake2b_init_key(&S, outlen, key, keylen);
    else
        ret = blake2b_init(&S, outlen);

    if (ret < 0) return ret;

    ret = blake2b_update(&S, in, inlen);
    if (ret < 0) return ret;

    return blake2b_final(&S, out, outlen);
}
