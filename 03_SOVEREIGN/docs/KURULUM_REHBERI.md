# ⚔ SOVEREIGN — Kişisel Kurulum Rehberi (Windows 10/11)

> Benim için özel hazırlandı. Adım adım, hiçbir şeyi atlama.

---

## ÖNEMLİ: Windows'ta Ne Çalışır, Ne Çalışmaz?

| Özellik | Windows Durumu |
|---|---|
| **GUI (tüm 7 panel)** | ✅ Tam çalışır |
| **AI Gateway (Claude→Kimi)** | ✅ Tam çalışır |
| **Discover (port tarayıcı)** | ✅ Tam çalışır |
| **Vault (şifreli key store)** | ✅ Tam çalışır |
| **Intel (analitik)** | ✅ Tam çalışır |
| **Forge — Sertifika üretimi** | ✅ Tam çalışır |
| **Forge — Sistem trust store** | ✅ `certutil` ile çalışır (Admin ister) |
| **Forge — Hosts file** | ✅ `C:\Windows\System32\drivers\etc\hosts` |
| **INTERCEPT (MITM proxy)** | ⚠️ Çalışır ama ekstra adım lazım (aşağıda) |
| **Streams (WS monitor)** | ✅ Proxy çalışırsa çalışır |

**Özet:** Claude→Kimi yönlendirmesi dahil her şey Windows'ta çalışır.  
Tek fark: MITM proxy için port 443 yerine 8080 kullanıyoruz ve sistem proxy'sini elle ayarlıyoruz.

---

## Adım 1 — Python Kurulumu

**Python 3.11 veya üstü lazım.** 3.12 önerilir.

```
https://www.python.org/downloads/windows/
```

Kurulum sırasında **"Add Python to PATH"** kutusunu mutlaka işaretle!

Kontrol:
```cmd
python --version
# Python 3.12.x çıkmalı
```

---

## Adım 2 — Proje Dosyalarını Aç

```cmd
cd %USERPROFILE%\Desktop
mkdir SOVEREIGN
cd SOVEREIGN
:: Zip'i buraya çıkart
```

---

## Adım 3 — Virtual Environment Oluştur

```cmd
python -m venv .venv
.venv\Scripts\activate
:: Artık (.venv) görünmeli promptta
```

---

## Adım 4 — Bağımlılıkları Kur

```cmd
pip install -r requirements.txt
```

İnternete göre 2-5 dakika sürer. Hata varsa:
```cmd
pip install --upgrade pip
pip install -r requirements.txt
```

---

## Adım 5 — API Key'i Ayarla

```cmd
set KIMI_API_KEY=sk-kimi-dzDEQ4PDe0K2IZ0WNvbuN4aIfKKajTJFlvPxmUwhZVdXGZBkhiqfCD2yUjFeVqlo
```

Her seferinde yazmamak için kalıcı yapalım:

1. Windows + R → `sysdm.cpl` → Gelişmiş → Ortam Değişkenleri
2. "Yeni" → Değişken adı: `KIMI_API_KEY`
3. Değer: `sk-kimi-dzDEQ4PDe0K2IZ0WNvbuN4aIfKKajTJFlvPxmUwhZVdXGZBkhiqfCD2yUjFeVqlo`
4. Tamam → Tamam

---

## Adım 6 — Çalıştır

```cmd
python main.py
```

Pencere açılmalı. Açılmazsa:
```cmd
python -c "from PyQt6.QtWidgets import QApplication; print('Qt OK')"
```

---

## Claude Desktop → Kimi Yönlendirmesi (Windows)

### Yol A: Gateway Only (Tavsiye Edilen, Kolay)

Port 443 MITM yerine doğrudan gateway kullan:

1. SOVEREIGN aç → **🟢 GATEWAY** panel
2. **⇄ Routes** tab → **＋ New** tıkla
3. Form doldur:
   - Route Name: `Claude → Kimi`
   - Intercept Host: `api.anthropic.com`
   - Forward To: `http://127.0.0.1:4000`
   - Backend Provider: `kimi`
   - Target Model: `kimi-for-coding`
   - Inject API Key: `sk-kimi-...` (senin key'in)
4. **💾 Save Route** tıkla
5. **■ GATEWAY** servis satırından **▶ START**

Sonra Hosts file ekle (Admin CMD):
```cmd
notepad C:\Windows\System32\drivers\etc\hosts
```
En alta ekle:
```
127.0.0.1 api.anthropic.com
```

### Yol B: Wizard (Otomatik, Admin Gerektirir)

1. SOVEREIGN'i **yönetici olarak çalıştır**:
   ```cmd
   :: Başlat menüsünde CMD'ye sağ tıkla → Yönetici olarak çalıştır
   cd %USERPROFILE%\Desktop\SOVEREIGN
   .venv\Scripts\activate
   python main.py
   ```
2. **🟡 FORGE** → **⚡ Claude→Kimi Wizard** tab
3. Kimi API Key gir
4. **⚡ RUN WIZARD** tıkla
5. 7 adım otomatik çalışır

### MITM Proxy (İleri Seviye, Windows)

Port 443'ü doğrudan dinlemek için:

```cmd
:: Admin CMD'de:
netsh http add urlacl url=https://+:443/ user=%USERNAME%

:: Başlat proxy:
set SOVEREIGN_PROXY_PORT=8080
python main.py
```

Sonra Windows sistem proxy ayarı:
- Ayarlar → Ağ → Proxy → Manuel proxy
- Adres: `127.0.0.1`, Port: `8080`

VEYA sadece Claude Desktop için (daha temiz):
```cmd
set HTTPS_PROXY=http://127.0.0.1:8080
"C:\Users\%USERNAME%\AppData\Local\AnthropicClaude\claude.exe"
```

---

## Sık Karşılaşılan Windows Hataları

### `ModuleNotFoundError: No module named 'PyQt6'`
```cmd
.venv\Scripts\activate    ← bunu unutmuşsun
pip install PyQt6
```

### `PermissionError: [WinError 5] Access is denied` — Hosts file
```cmd
:: Notepad'i Admin olarak aç
:: Ya da PowerShell Admin ile:
Add-Content -Path "C:\Windows\System32\drivers\etc\hosts" -Value "127.0.0.1 api.anthropic.com"
```

### Proxy port hatası — `[WinError 10013] An attempt was made to access a socket in a way forbidden`
```
Port 443 Windows'ta özel izin gerektirir.
Çözüm: SOVEREIGN_PROXY_PORT=8080 kullan (yukarıda anlattım)
```

### `mitmproxy` yükleme hatası
```cmd
pip install mitmproxy --only-binary :all:
:: Olmadıysa:
pip install mitmproxy==10.4.2
```

### Sertifika hatası Claude Desktop'ta
```
Adım 5'te CA'yı sistem trust store'a yüklemeyi unuttun.
FORGE → Certificates → SOVEREIGN Root CA → ↑ Trust CA
:: VEYA Admin CMD:
certutil -addstore "Root" "%USERPROFILE%\.sovereign\certs\sovereign-ca.crt"
```

---

## Hızlı Başlatma Script'i (batch)

Masaüstüne `SOVEREIGN.bat` oluştur:

```batch
@echo off
cd /d "%~dp0"
call .venv\Scripts\activate
set KIMI_API_KEY=sk-kimi-dzDEQ4PDe0K2IZ0WNvbuN4aIfKKajTJFlvPxmUwhZVdXGZBkhiqfCD2yUjFeVqlo
set SOVEREIGN_PROXY_PORT=8080
python main.py
```

Çift tıkla, her seferinde hazır.

---

## Günlük Kullanım Akışı

```
1. SOVEREIGN.bat çalıştır
2. GATEWAY panel → ▶ START  (AI yönlendirme aktif)
3. Claude Desktop aç → otomatik Kimi'ye gider
4. INTEL panel → trafik analizi
5. VAULT → key'leri yönet
```

---

## Dosya Konumları (Windows)

| Dosya | Konum |
|---|---|
| Config | `C:\Users\SEN\.sovereign\config.toml` |
| Vault | `C:\Users\SEN\.sovereign\vault.json` |
| Sertifikalar | `C:\Users\SEN\.sovereign\certs\` |
| Log | `C:\Users\SEN\.sovereign\logs\sovereign.log` |
| Hosts backup | `C:\Windows\System32\drivers\etc\hosts.sovereign.bak` |

---

## Destek

Log dosyasına bak:
```cmd
type %USERPROFILE%\.sovereign\logs\sovereign.log
```
