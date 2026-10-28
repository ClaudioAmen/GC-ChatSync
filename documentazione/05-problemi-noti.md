# 05 - Problemi Noti & Roadmap

## 🐛 Problemi Risolti
* [x] **Crash encoding cp1252 su console Windows**: Risolto configurando UTF-8 forzato in `src/gc_chatsync/__init__.py` e `cli.py`.
* [x] **File lock `WinError 32` nei test unitari su Windows**: Risolto con chiusura esplicita delle connessioni SQLite e `gc.collect()` nei `tearDown`.
* [x] **Incompatibilità attributo `connected` in SyncStatus**: Aggiunte property di compatibilità (`connected`, `last_device`, `last_sync_utc`) in `SyncStatus`.
* [x] **Fallback Path.home() quando le variabili d'ambiente sono svuotate**: Gestito blocco try/except su `Path.home()` in `config.py`.
* [x] **Pop-up continui finestre CMD/Shell durante i trasferimenti**: Risolto con `subprocess.CREATE_NO_WINDOW` su tutte le chiamate esterne (Windows) e shortcut avviata direttamente con `pythonw.exe`.
* [x] **File .bak duplicati sul Desktop**: Reindirizzato il backup dei collegamenti in `AppData\Roaming\gc-chatsync\backups\shortcuts`.
* [x] **Timeout 180s e 10s per Rate Limit Google Drive (HTTP 403)**: Su trasferimenti multipli simultanei, il client ID condiviso di rclone veniva temporaneamente limitato da Google (pacing esponenziale fino a 32s). Risolto introducendo `--tpslimit 8`, riducendo workers a 2 e transfers a 2, e aumentando i timeout operativi (30s per il ping, 600s per il sync massivo).

---

## ⚠️ Limitazioni Note
1. **Editing Concorrente Simultaneo**: GC-ChatSync è progettato per un singolo utente che alterna il lavoro tra due computer (es. Desktop Windows $\rightarrow$ Laptop Linux). La modifica simultanea della stessa chat contemporaneamente su due PC accesi non è supportata dal merge e viene segnalata dal lock cloud.
2. **Setup Iniziale Google Drive**: Richiede una prima autenticazione interattiva tramite browser (`rclone config`). Non è possibile automatizzare l'OAuth Google senza interazione utente nel browser.

---

## 🗺️ Roadmap
- [ ] Completamento autorizzazione `rclone config` con account Google Drive dell'utente.
- [ ] Creazione del primo backup e push di baseline su Google Drive.
- [ ] Creazione della shortcut sul Desktop o collegamento dell'icona abituale di Antigravity.
- [ ] Configurazione della macchina secondaria (Laptop Linux) con gli stessi parametri di sync.
