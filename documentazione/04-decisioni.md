# 04 - Registro delle Decisioni Architetturali (ADR)

## ADR-001: Utilizzo di Rclone per la Sincronizzazione Google Drive
* **Stato**: Accettato
* **Contesto**: Google Drive non fornisce un client desktop ufficiale per Linux, e i client generali (Drive for Desktop, OneDrive) corrompono i database SQLite WAL e sovrascrivono ciecamente l'indice delle conversazioni.
* **Decisione**: Usare il CLI maturo e open-source `rclone` per gestire i trasferimenti verso Google Drive, gestendo da Python la logica applicativa (checkpointing WAL, merge SQLite, backup).
* **Conseguenze**: Nessuna dipendenza da librerie Python pesanti (come google-api-python-client con centinaia di transitive dependencies); massima velocità con rclone compilato in Go.

---

## ADR-002: SQLite Online Backup API e WAL Checkpointing
* **Stato**: Accettato
* **Contesto**: Copiare file `.db` mentre un'applicazione è aperta può catturare uno stato corrotto o incompleto se il file `-wal` contiene transazioni non consolidate.
* **Decisione**: Eseguire `PRAGMA wal_checkpoint(TRUNCATE)` alla chiusura di Antigravity, e utilizzare `conn.backup(dest_conn)` per generare snapshot puliti e coerenti.
* **Conseguenze**: Zero corruzione nei database trasferiti sul cloud.

---

## ADR-003: Reconfigurazione UTF-8 Obbligatoria su Windows
* **Stato**: Accettato
* **Contesto**: Su console Windows con codepage legacy (cp1252), la stampa di emoji (🚀, 🌌, ❌) provocava un crash irreversibile `UnicodeEncodeError`.
* **Decisione**: Reconfigurare `sys.stdout` e `sys.stderr` in UTF-8 con fallback safe (`replace`) a livello di `__init__.py` e `cli.py`.
* **Conseguenze**: Piena stabilità e visualizzazione pulita su PowerShell e Windows CMD.

---

## ADR-004: Chiusura Esplicita delle Connessioni SQLite nei Test su Windows
* **Stato**: Accettato
* **Contesto**: Su Windows, `with sqlite3.connect(...) as conn` non chiude la connessione (gestisce solo la transazione), mantenendo un lock esclusivo sul file che faceva fallire la pulizia delle directory temporanee nei test (`WinError 32`).
* **Decisione**: Chiusura esplicita `conn.close()` e invocazione di `gc.collect()` nei `tearDown` dei test.
* **Conseguenze**: Suite di test al 100% verde (60/60) sia su Windows che su Linux.
