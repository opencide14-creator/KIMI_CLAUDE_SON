---
name: swarm-patterns
description: UltraOrchestrator orchestration patterns deep-dive. Usage: /swarm-patterns [pattern-name]
argument-hint: "[pattern: token-resonance|circuit-breaker|rate-limit-balancing|quality-gate-layers|batch-optimization|key-rotation|retry-escalation|sandbox-evasion|checkpoint-recovery]"
allowed-tools: ["Read"]
---

# /swarm-patterns Komutu

UltraOrchestrator'un orkestrasyon pattern'lerinin en derin teknik analizini saglar.

## Pattern'ler

### token-resonance
- **Amaç**: API key'ler arasinda token kullanimini harmonik olarak dengelemek
- **Mekanizma**: Her key'in TPM (tokens per minute) kullanimi sinüzoidal bir dalga olarak modellenir
- **Faz kaydirma**: Key'ler birbirinden 90 derece faz farkiyla calisir, böylece toplam TPM düz bir cizgi olur
- **Formül**: `optimal_tokens = (tpm_limit * safety_margin) / active_keys * phase_factor`
- **Avantaj**: Rate limit hiçbir key'de ayni anda tetiklenmez
- **Implementasyon**: `core.py` içinde `TokenResonanceStrategy` sinifi

### circuit-breaker
- **Amaç**: Ard arda 429/5xx hatalarinda sistemi korumak
- **Mekanizma**: `circuit_open_after_429s`: 5
- **Durumlar**: CLOSED (normal), OPEN (bloklu), HALF-OPEN (test)
- **OPEN süresi**: `retry_base_delay * 2^n` exponential backoff
- **HALF-OPEN**: Tekrar deneme, basarili olursa CLOSED
- **Avantaj**: Ölü key'ler otomatik devre disi kalir, diger key'ler yükü alir

### rate-limit-balancing
- **Amaç**: RPM ve TPM limitlerini ayni anda optimize etmek
- **Mekanizma**: Her key icin iki ayrı kova (token kovasi, request kovasi)
- **Leaky bucket**: Her saniye kova sabit hizla dolar
- **Tahmin**: Bir sonraki request'in ne zaman güvenli oldugunu hesapla
- **Global limit**: `global_concurrent_limit: 40` tüm key'ler toplami
- **Per-key limit**: `per_key_concurrent: 10`

### quality-gate-layers
- **Layer 0 — Existence**: Dosya bos mu, null mu kontrolü
- **Layer 1 — Anti-Smell**: Banned patterns taramasi (mock, placeholder, bare except, vb.)
- **Layer 2 — Criteria**: Fonksiyon uzunlugu, complexity, docstring varligi
- **Layer 3 — Sandbox**: Güvenlik taramasi (os.system, eval, subprocess, vb.)
- **Layer 4 — Dedup**: Benzer kod bloklarini tespit et, `DUPLICATE_SIMILARITY_THRESHOLD: 0.9`
- **Bypass**: Hicbir layer bypass edilemez. Hepsi `true` olmalı.

### batch-optimization
- **Amaç**: Paralel request'leri en verimli gruplamak
- **Mekanizma**: `batch_size_max: 40`, `batch_size_min: 1`
- **Dinamik batch size**: Mevcut rate limit durumuna göre ayarlanir
- **Token bazli batching**: Bir batch'in toplam token'i TPM limitinin %80'ini gecmez
- **Hata izolasyonu**: Bir batch elemani basarisiz olursa digerleri etkilenmez

### key-rotation
- **Amaç**: Esit yük dagilimi ve key sagligi izleme
- **Mekanizma**: Priority sirasina göre basla, alt key'lere yük devret
- **Health check**: Her key'in son 5 request'i basarili mi?
- **Otomatik devre disi**: 3 basarisiz denemeden sonra key listeden cikarilabilir
- **Re-activation**: 60 saniye sonra tekrar dene

### retry-escalation
- **Amaç**: Her hata turune özel retry stratejisi
- **Mekanizma**: `max_retries: 7`
- **429 Too Many Requests**: Exponential backoff, base 2.0s
- **5xx Server Error**: Linear backoff + key degisimi
- **Timeout**: Immediate retry baska key ile
- **Connection Error**: 5 saniye bekle, tüm key'lerle dene
- **Final failure**: Kullaniciya bildir, checkpoint'ten devam et

### sandbox-evasion
- **Amaç**: Kötü niyetli kod tespiti ve engelleme
- **Mekanizma**: `BLOCKED_PATTERNS` listesi runtime'da güncellenebilir
- **Regex tabanli**: `os\.system\s*\(`, `eval\s*\(`, `subprocess\.Popen\s*\(`, vb.
- **Description**: Her pattern'in neden engellendigi aciklamasi
- **GUI edit**: Settings panelinden QPlainTextEdit ile düzenlenebilir
- **Persist**: `default_config.yaml`'in `sandbox_patterns` section'ina kaydedilir

### checkpoint-recovery
- **Amaç**: Görev yarıda kesilirse kaldigi yerden devam etmek
- **Mekanizma**: Her `checkpoint_interval` (default 5) saniyede durum kaydet
- **Checkpoint format**: JSON — tamamlanan subtask'ler, kalan kuyruk, mevcut key durumu
- **Recovery**: Baslangicta checkpoint dosyasi varsa, durumu yükleyip devam et
- **Atomic write**: Temp file → rename ile bozulma önlenir

## Output Format

Kullanicinin sectigi pattern'in:
1. Amacini ve motivasyonunu
2. Algoritma/mekanizma detaylarini
3. Matematiksel formülleri (varsa)
4. Implementasyon referanslarini (hangi dosya, hangi sinif, hangi metod)
5. Avantaj ve dezavantajlari
6. Edge case'leri ve cozumlerini

Eger pattern belirtilmemisse, tüm pattern'leri kisa kisa listele ve en popüler 3'unü özetle.