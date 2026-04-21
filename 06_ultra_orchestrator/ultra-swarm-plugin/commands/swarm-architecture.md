---
name: swarm-architecture
description: Deep technical reference for UltraOrchestrator architecture. Usage: /swarm-architecture [topic]
argument-hint: "[topic: core|sandbox|quality-gate|checkpoint|rate-limiter|api-keys]"
allowed-tools: ["Read"]
---

# /swarm-architecture Komutu

UltraOrchestrator'un mimarisi hakkinda en derin teknik detaylari saglar. Kullanici `/swarm-architecture` veya `/swarm-architecture sandbox` yazdiginda calisir.

## Konular

### core
- `orchestrator/core.py` — SwarmEngine sinifi
- `update_settings()` metodu: settings dict'ini alir, internal vars günceller, sandbox_executor ve quality_gate'i yeni pattern'lerle günceller
- `_max_concurrent`, `_max_retries`, `_timeout`, `_safety_margin`, `_checkpoint_interval`
- Task kuyrgu yönetimi, batching, token resonance stratejisi
- Signal-slot mimarisi: `settings_saved` → `main_window._on_settings_saved()` → `core.update_settings()`

### sandbox
- `infrastructure/sandbox_executor.py` — SandboxExecutor sinifi
- `BLOCKED_PATTERNS`: regex + description tuple listesi
- `WARNED_PATTERNS`: benzer yapida ama uyari seviyesinde
- `check_safety_patterns()`: kaynak kodu regex ile tarar
- `scan_safety_sync()`: senkron tarama
- `__init__`'e `blocked_patterns: Optional[List[Tuple[str, str]]] = None` parametresi
- Config'den dinamik pattern yüklenmesi

### quality-gate
- `orchestrator/quality_gate.py` — QualityGate sinifi
- 4 katman: existence, anti-smell, criteria, sandbox, dedup
- `BANNED_PATTERNS`: dict[str, str] — regex → description
- `DUPLICATE_SIMILARITY_THRESHOLD`: default 0.9
- `MAX_UNUSED_IMPORT_RATIO`: default 0.5
- `banned_patterns`, `duplicate_similarity_threshold`, `max_unused_import_ratio` init parametreleri
- Layer'lar boolean olarak enable/disable edilebilir

### checkpoint
- Checkpoint sistemi: her N saniyede (default 5) durum kaydi
- `checkpoint_interval` config'den okunur
- Checkpoint dosyalari JSON formatinda
- Task recovery: checkpoint'ten devam etme
- `checkpoint_file` her task'in meta verisinde tutulur

### rate-limiter
- `rate_limit_strategy`: token_resonance (default)
- `safety_margin`: 0.8 (default)
- `max_tokens_per_minute`: 200000 per key
- `max_requests_per_minute`: 60 per key
- `global_concurrent_limit`: 40
- `per_key_concurrent`: 10
- Circuit breaker: `circuit_open_after_429s`: 5
- `retry_base_delay`: 2.0 saniye
- `max_retries`: 7

### api-keys
- `kimi_api_keys` listesi: id, key, tier, max_tokens_per_minute, max_requests_per_minute, priority
- Key'ler `${ENV_VAR}` formatinda env variable referansi
- Priority sirasina göre kullanilir (1 = en yüksek)
- Tier: coding, analysis, vb.
- Runtime'da `os.environ.get()` ile çözülür

## Output Format

Kullanicinin sectigi konuya göre derin teknik dökümantasyon sun. Her konu basligi altinda:
- Sinif ve metod referanslari
- Config parametreleri ve default degerleri
- Veri akisi (data flow)
- Hata durumlari ve handling
- Performance karakteristikleri

Eger konu belirtilmemisse, tüm mimariyi high-level özetle ve mevcut konulari listele.