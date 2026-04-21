# UltraSwarm Plugin

Claude Code entegrasyonu için UltraOrchestrator swarm yönetim plugin'i.

## Özellikler

- **/swarm** — UltraOrchestrator'a görev gönder, takibe başla
- **/swarm-architecture** — Mimari detaylar (core, sandbox, quality-gate, checkpoint, rate-limiter, api-keys)
- **/swarm-patterns** — Orkestrasyon pattern'leri (token-resonance, circuit-breaker, batch-optimization, vb.)
- **task-tracker agent** — Checkpoint dosyalarını izleyerek proaktif durum raporu
- **swarm-executor agent** — Görev çalıştırma, API key yönetimi, retry logic
- **Hook'lar** — SessionStart doğrulama, Bash öncesi uyarı, Stop kontrolü, checkpoint hatırlatma
- **MCP Server** — 8 araç: submit_task, get_task_status, list_active_tasks, get_quality_report, get_sandbox_logs, cancel_task, update_config, get_config

## Kurulum

### 1. Plugin Dizinini Kopyala

```bash
cc --plugin-dir "C:\Users\<USERNAME>\path\to\ultra-swarm-plugin"
```

### 2. Ayarları Yapılandır

`%USERPROFILE%\.claude\ultra-swarm.local.md` dosyasını oluştur:

```markdown
# UltraSwarm Local Settings

## Paths
- **EXE Path**: `C:\Users\<USERNAME>\path\to\UltraOrchestrator.exe`
- **Config Path**: `C:\Users\<USERNAME>\path\to\default_config.yaml`
- **Python Path**: `C:\Users\<USERNAME>\path\to\ultra_orchestrator`

## Behavior
- **Auto Checkpoint**: true — Her kritik dosya değişikliğinde checkpoint öner
- **Notify on Complete**: true — Görev tamamlandığında bildir
- **Notify on Error**: true — Hata oluştuğunda hemen bildir
- **Track Milestones**: [25, 50, 75, 90, 100] — Hangi yüzdeliklerde rapor verilecek

## API Keys
- Env var prefix: `KIMI_KEY_`
- Aktif key'ler: KEY_1, KEY_2, KEY_3, KEY_4
```

### 3. Ortam Değişkenleri

```powershell
$env:ULTRA_EXE_PATH = "C:\Users\<USERNAME>\path\to\UltraOrchestrator.exe"
$env:ULTRA_CONFIG_PATH = "C:\Users\<USERNAME>\path\to\default_config.yaml"
$env:ULTRA_SWARM_HOME = "$env:USERPROFILE\.claude\ultra-swarm"
$env:PYTHONPATH = "C:\Users\<USERNAME>\path\to\ultra_orchestrator"
```

## Kullanım

### Görev Gönderme

```
/swarm "Tüm GUI panellerine dark mode ekle, settings.py'den başla"
```

Opsiyonel parametreler:
```
/swarm "Görev açıklaması" --priority=1 --keys=KEY_1,KEY_2 --tier=coding --timeout=300
```

### Mimari Bilgi

```
/swarm-architecture sandbox
/swarm-architecture quality-gate
```

### Pattern Bilgi

```
/swarm-patterns token-resonance
/swarm-patterns circuit-breaker
```

### MCP Araçları

MCP araçları otomatik yüklenir. `/mcp` komutu ile görülebilir.

```
# Görev gönder (MCP aracılığıyla)
mcp__plugin_ultra_swarm_ultra_swarm_mcp__submit_task

# Durum sorgula
mcp__plugin_ultra_swarm_ultra_swarm_mcp__get_task_status

# Aktif görevleri listele
mcp__plugin_ultra_swarm_ultra_swarm_mcp__list_active_tasks
```

## Dosya Yapısı

```
ultra-swarm-plugin/
├── .claude-plugin/
│   └── plugin.json          # Plugin manifest
├── commands/
│   ├── swarm.md             # /swarm komutu
│   ├── swarm-architecture.md # /swarm-architecture
│   └── swarm-patterns.md    # /swarm-patterns
├── agents/
│   ├── swarm-executor.md    # Görev çalıştırma agent'ı
│   └── task-tracker.md      # Checkpoint izleme agent'ı
├── hooks/
│   └── hooks.json           # Event hook'ları
├── scripts/
│   ├── mcp-server.py        # MCP stdio server
│   ├── session-start.py     # SessionStart hook script
│   ├── pre-bash-check.py    # PreToolUse Bash kontrol
│   └── stop-check.py        # Stop hook script
├── .mcp.json                # MCP server konfigürasyonu
└── README.md                # Bu dosya
```

## Hook'lar

| Event | Tip | Açıklama |
|-------|-----|----------|
| SessionStart | Command | EXE, config, dizin doğrulama |
| UserPromptSubmit | Prompt | Aktif görev varsa nazikçe hatırlat |
| PreToolUse (Bash) | Command | Orchestrator çalıştırılırken uyarı |
| PostToolUse (Write\|Edit) | Prompt | Kritik dosya değişikliğinde checkpoint öner |
| Stop | Command + Prompt | Görev tamamlanma kontrolü |

## Sorun Giderme

**"UltraOrchestrator.exe bulunamadı"**
- `.claude/ultra-swarm.local.md` dosyasındaki `exe_path`'i kontrol et
- `$env:ULTRA_EXE_PATH` doğru ayarlandığından emin ol

**MCP server bağlanmıyor**
- `python scripts/mcp-server.py` manuel çalıştırarak test et
- `claude --debug` ile detaylı logları gör

**Hook'lar çalışmıyor**
- Claude Code'u yeniden başlat (hook değişiklikleri otomatik yüklenir)
- `claude --debug` ile hook kayıtlarını kontrol et

## Lisans

Sovereign — Komutan ALUVERSE
