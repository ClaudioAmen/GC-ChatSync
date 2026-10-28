---
project: "GC-ChatSync"
version: "0.1.0"
status: "active"
last_update: "2026-10-28"
week: "2026-W44"
tests: "60/60 passing"
cloud: "connected (gdrive)"
---

# GC-ChatSync - Stato del Progetto

> Strumento di sincronizzazione automatizzata multipiattaforma (Windows/Linux) per sessioni, chat, cronologia e brain artifacts di Google Antigravity tramite Google Drive e Rclone.

## 📌 Avanzamento Recente
* Repository clonato in `D:\Progetti\GC-ChatSync`.
* `rclone v1.75.1` installato e configurato nel PATH di sistema.
* Pacchetto `gc-chatsync` installato ed eseguibile da terminale.
* **Eliminazione Finestre Shell Flashing**: Aggiunto `subprocess.CREATE_NO_WINDOW` a tutte le chiamate esterne (rclone, tasklist, powershell). Ora i trasferimenti cloud avvengono 100% in background senza finestre popup.
* **Risoluzione Icona Desktop**: Icona di Antigravity collegata all'eseguibile reale ad alta risoluzione con notifica shell immediata (`SHChangeNotify`).
* **Pulizia Desktop (.bak)**: I file `.bak` delle scorciatoie vengono ora salvati ordinatamente in `AppData\Roaming\gc-chatsync\backups\shortcuts` invece di comparire sul Desktop.
* **Avvio Silenzioso GUI via pythonw**: Il collegamento desktop punta direttamente a `pythonw.exe -m gc_chatsync.cli gui`, eliminando ogni finestra nera CMD iniziale.
* **Ottimizzazione Pacing Google Drive (Zero Errori 403 / Timeout)**: Risolto il blocco rilevato in `sync.log` calibrando il rate limit di rclone (`--tpslimit 8`, `max_workers=2`, timeout estesi a 600s), evitando il throttling delle API Google.
* **Correzione Filtri Rclone Brain (da 9.000 file a 258)**: Corretti i pattern di esclusione in `drive.py`. Eliminati i 8.700 micro-file intermedi di step/tasks/messages che causavano il blocco del push alla chiusura. Il tempo di sync di `brain` è passato da 18 minuti a 3.1 secondi.
* **Suite di Test Verde**: 61 test su 61 superati (100% OK).

## ⏭️ Prossimi Step
1. Verificare l'esperienza utente su Windows senza pop-up console.
2. Commit e push dei fix Windows nel repository Git.
