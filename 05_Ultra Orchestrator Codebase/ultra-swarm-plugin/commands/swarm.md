---
name: swarm
description: Submit a task to UltraOrchestrator swarm. Usage: /swarm "task description" [--priority=N] [--keys=KEY_1,KEY_2]
argument-hint: "<gorev aciklamasi> [--priority=1-5] [--keys=KEY_LIST]"
allowed-tools: ["Read", "Write", "Bash", "Grep"]
---

# /swarm Komutu

Kullanici `/swarm "<gorev>"` yazdiginda bu komut calisir. Gorevi UltraOrchestrator'a ilet, takibe basla, kullaniciya geri bildirim ver.

## Adimlar

1. **Ortam Dogrulama**
   - `ULTRA_EXE_PATH` environment variable veya settings'ten EXE yolunu oku
   - EXE'nin varligini kontrol et. Yoksa hata ver: "UltraOrchestrator.exe bulunamadi. Lutfen .claude/ultra-swarm.local.md'de exe_path ayarlayin."
   - Config dosyasinin varligini ve okunabilirligini kontrol et
   - `.claude/ultra-swarm/` dizinini olustur (yoksa)
   - `tasks/` ve `logs/` alt dizinlerini olustur

2. **Gorev Parametrelerini Ayikla**
   - Gorev aciklamasi (zorunlu)
   - `--priority`: 1-5 arasi, default 3
   - `--keys`: Kullanilacak API key ID'leri, virgulle ayrilmis. Default: config'deki tum key'ler
   - `--tier`: coding / analysis / default: coding
   - `--timeout`: Saniye cinsinden max sure, default 180

3. **Config Yükle ve Görevi Olustur**
   - `default_config.yaml`'i oku
   - API key'leri ve limitleri al
   - `tasks/task_{timestamp}_{shortid}.json` dosyasina gorev meta verisini yaz:
     ```json
     {
       "task_id": "ts_shortid",
       "description": "...",
       "status": "queued",
       "priority": 3,
       "keys": ["KEY_1"],
       "submitted_at": "ISO8601",
       "checkpoint_file": "tasks/task_{id}_checkpoint.json",
       "log_file": "logs/task_{id}.log"
     }
     ```

4. **UltraOrchestrator'u Baslat**
   - EXE'yi subprocess olarak calistir:
     ```
     UltraOrchestrator.exe --task-file tasks/task_{id}.json --config default_config.yaml
     ```
   - Eger EXE `--task-file` desteklemiyorsa, Python modulunu dogrudan calistir:
     ```
     python -m ultra_orchestrator --task-file tasks/task_{id}.json
     ```
   - Process ID'yi (PID) kaydet, gorev meta verisine ekle

5. **Kullaniciya Yanit Ver**
   - Kisa ozet: "Swarm gorevi baslatildi. Task ID: `{task_id}`. Oncelik: {priority}. Kullanilan key'ler: {keys}."
   - "Durum takibi icin `task-tracker` agent'i aktif ediliyor..."
   - Kullaniciya sor: "Gorevin ilerlemesini detayli takip etmemi ister misiniz, yoksa arka planda calissin mi?"

6. **Hata Durumlari**
   - EXE bulunamazsa → acik hata mesaji + ayar yönergesi
   - Config okunamazsa → hata mesaji + config yolu kontrolü önerisi
   - Subprocess baslatilamazsa → log dosyasina yaz, kullaniciya bilgi ver
   - API key yoksa → uyari ver ama gorevi yine de kuyruga al (EXE kendi halletsin)

## Output Format

```markdown
## Swarm Gorevi Baslatildi

- **Task ID**: `task_20260421_143052_a7b3`
- **Aciklama**: {gorev ozeti}
- **Oncelik**: {priority}
- **API Key'ler**: {keys}
- **Durum**: queued → running
- **Log**: `.claude/ultra-swarm/logs/task_{id}.log`
- **Checkpoint**: `.claude/ultra-swarm/tasks/task_{id}_checkpoint.json`

Takip etmek icin: `/swarm-status {task_id}` veya agent `task-tracker`'i aktif edin.
```

## Notlar

- Her `/swarm` komutu benzersiz bir task ID üretir
- Görevler asenkron calisir; bu komut hemen doner (bloklamaz)
- Log dosyasi real-time güncellenir; `Read` tool ile okunabilir
- Checkpoint dosyasi her ilerleme kaydedildiginde güncellenir
- `task-tracker` agent'i proaktif olarak checkpoint degisikliklerini izler