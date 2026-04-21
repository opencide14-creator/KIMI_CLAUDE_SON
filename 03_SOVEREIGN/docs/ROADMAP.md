# SOVEREIGN — Dürüst Audit + Roadmap

## Gerçek Durum (10 Nisan 2026)

### ✅ Gerçekten Çalışan
- Proxy engine başlar (RUNNING durumuna geçer)
- Gateway FastAPI /health endpoint'i cevap verir
- Vault şifreleme/çözme
- Cert Authority X.509 üretimi
- Intercept panel traffic inject aldığında gösterir
- State yayını (SK.TRAFFIC_NEW → panel)
- Tüm 8 panel instantiate olur (crash etmez)

### ❌ Broken / Yanlış Davranış

#### Kritik Buglar
1. **route.summary() TypeError** — GatewayRoute.summary `@property` ama `route.summary()` olarak çağrılıyor → gateway route eklenemiyor
2. **Nmap gerçek ağda çalışmıyor ama sandbox'ta değil** — routing sorunu sandbox'a özgü, Windows'ta çalışacak
3. **Dashboard proxy start butonu port değişikliğini yansıtmıyor** — port spinbox değeri ProxyEngine._port'a yazılmıyor
4. **Gateway _find_route_for_model logic kırık** — alias eşleşmesi yanlış, Kimi route isteği karşılamıyor

#### Eksik Bağlantılar (Panel-Panel)
5. **Kimi API Key'i hiçbir yere bağlı değil** — Vault'a koyuyorsun ama Gateway route'u otomatik kullanmıyor
6. **Discover → Gateway** — bir service'e tıklayınca Gateway Route ekle çalışır görünüyor ama source_host boş kalıyor
7. **Intel panel** — TRAFFIC_ENTRIES state'den okuyor, panel oluştururken değil runtime'da subscribe ediyor, ilk 50 request kaçıyor
8. **Forge wizard** — 7 adım thread'de çalışıyor ama step_update Qt sinyali olmadan çağrılıyor (cross-thread UI crash riski)
9. **Streams panel** — WsConnection monitor wiring var ama ProxyEngine.ws_frame → StreamMonitor → StreamsPanel zinciri MainWindow'da yok

#### Hiç Yok
10. **AI Chat** — Kimi/lokal LLM ile entegre chat yok
11. **Context-aware wizard** — service'e sağ tıklayınca akıllı öneri yok
12. **Persistent storage** — scan sonuçları, route'lar, ayarlar program kapanınca gidiyor
13. **Real Windows installer for tools** — winget/choco command doğru ama test edilmedi

---

## ROADMAP — Öncelik Sırasıyla

### TIER 0 — Buglar (Şimdi Fix Edilecek)

| # | Bug | Dosya | Efor |
|---|-----|-------|------|
| B1 | route.summary() → route.summary | router.py | 1 satır |
| B2 | Gateway model matching | router.py | 20 satır |
| B3 | Dashboard port sync | dashboard/panel.py | 5 satır |
| B4 | Streams monitor wiring | main_window.py | 5 satır |
| B5 | Forge wizard cross-thread UI | forge/panel.py | 10 satır |
| B6 | Intel initial subscription | intel/panel.py | 5 satır |

### TIER 1 — AI Chat Panel (Bir Sonraki Build)

```
┌─────────────────────────────────────────────┐
│  🤖  SOVEREIGN AI ASSISTANT                  │
│                                             │
│  [Chat history]                             │
│  Sen: "scan 192.168.1.0/24"                 │
│  AI:  Scanning... (runs nmap)               │
│       Found 12 hosts. Shall I run vuln scan?│
│                                             │
│  ─────────────────────────────────────────  │
│  [Type here...]              [Send] [Tools] │
└─────────────────────────────────────────────┘
```

- Kimi API (KIMI_API_KEY) veya lokal llama.cpp
- SOVEREIGN tool'larına tam erişim (nmap, proxy, gateway, vault)
- Persistent konuşma geçmişi (SQLite)
- Tool call display (nmap çalıştırdığında sonuçları gösterir)

### TIER 2 — Persistent State

- `~/.sovereign/state.json` — routes, hosts, vault
- Scan sonuçları SQLite'a → Intel grafikler tarihsel
- Config hot-reload

### TIER 3 — Context-Aware Wizard

Service'e sağ tıklayınca:
```
⬡ 192.168.1.5:8080 (FastAPI)
  ├─ 🔍 Run NSE: HTTP Enum
  ├─ → Add as Gateway Route
  ├─ ⚡ Route to Kimi (wizard)
  ├─ 🤖 Ask AI about this service
  └─ 📋 Copy address
```

### TIER 4 — Real Tool Integration

- **masscan** — parallel high-speed scan (kendi implementasyonu)
- **whatweb** — web fingerprint entegrasyonu
- **nikto** — vuln scan output parsing
- Windows: chocolatey/winget test + doğrudan .exe download

---

## Mimari Sorunları

### Şu An (Kırık)
```
MainWindow.proxy.request_captured → lambda → state.TRAFFIC_NEW
                                                    ↓
                                         InterceptPanel._on_new_entry
                                         (state subscriber)
```

Problem: MainWindow'da `_make_intercept()` her çağrıldığında yeni panel oluşturuyor ama eski panel'in subscription'ı kalmaya devam ediyor. Bellek sızıntısı.

### Olması Gereken
```
MainWindow
  ├─ proxy.request_captured → intercept_panel.inject_entry (direct)
  ├─ proxy.response_captured → intercept_panel.on_entry_updated (direct)
  ├─ proxy.ws_* → stream_monitor.on_ws_* (direct)
  ├─ gateway.request_routed → intel_panel._on_routed (direct)
  └─ discovery.service_found → discover_panel._on_service (direct)

State sadece cross-panel broadcast için:
  SK.TRAFFIC_ENTRIES → Intel (aggregated view)
  SK.GATEWAY_ROUTES → Dashboard (count display)
```

### AI Chat Mimarisi (Tier 1)
```
ChatPanel
  ├─ KimiClient (httpx, streaming)
  │   └─ KIMI_API_KEY from VaultStore
  ├─ ToolExecutor
  │   ├─ tool: nmap_scan(target, args) → NmapScanWorker
  │   ├─ tool: proxy_start(port) → ProxyEngine
  │   ├─ tool: gateway_add_route(route) → GatewayRouter
  │   ├─ tool: vault_get(key) → VaultStore
  │   └─ tool: read_file(path) / write_file(path, content)
  └─ ConversationStore (SQLite, persistent)
```

---

## Nmap Windows Notları

Sandbox'ta `setup_target: failed to determine route to 127.0.0.1` hatası network isolation'dan geliyor — gerçek Windows/Linux makinede olmaz. Tool şu şekilde test et:

```bash
# Windows'ta:
nmap -Pn -sV -T4 192.168.1.1

# Linux'ta (gerçek ağ):
sudo nmap -sS -sV -T4 192.168.1.0/24
```

NSE presets production'da çalışır. Sandbox'ta test edilemiyor.
