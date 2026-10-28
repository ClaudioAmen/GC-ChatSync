# 03 - Funzionalità & Dettagli Tecnici

## 🧭 Flusso Operativo del Ciclo di Vita (Lifecycle)
1. **Pre-Check**:
   - Legge `manifest.json` da Google Drive.
   - Controlla epoch e identificativo dell'ultimo dispositivo.
   - Se il cloud è più recente, avvia il PULL con merge.
2. **PULL (Download & Merge)**:
   - Scarica delta di `conversations/` e `brain/` con `rclone copy --update`.
   - Crea un backup di sicurezza dell'indice SQLite locale `conversation_summaries.db`.
   - Esegue il merge incrementale dei record tramite SQLite `ATTACH DATABASE`.
   - Traduce i path dei workspace (`file:///C:/Users/...` $\leftrightarrow$ `file:///home/...`).
3. **Esecuzione Antigravity**:
   - Avvia l'applicazione desktop Antigravity e ne monitora il processo.
4. **POST-SYNC (Push dei Risultati)**:
   - Esegue `PRAGMA wal_checkpoint(TRUNCATE)` per svuotare i file `-wal`.
   - Crea uno snapshot consistente dell'indice con SQLite backup API.
   - Effettua il push in parallelo su Google Drive.
   - Incrementa l'epoch nel manifest cloud e rimuove il flag di sessione attiva.

---

## 🔀 Dettaglio Merge SQLite (`merger.py`)
- Rileva dinamicamente le colonne di `conversation_summaries` tra locale e remoto.
- Inserisce le sessioni nuove create sull'altro computer.
- Aggiorna i riassunti esistenti solo se `remote.last_modified_time > local.last_modified_time`.
- Sincronizza l'indice FTS (`conversations_fts`) se presente.
- Se viene rilevata un'incompatibilità di schema (colonne aggiunte da una nuova versione di Antigravity), abortisce sollevando `DatabaseMergeError` per proteggere l'integrità dei dati.

---

## 🖥️ Interfaccia GUI (`gui.py`)
- Mostra lo stato di connessione a Google Drive e l'ultimo dispositivo sincronizzato.
- Barra di avanzamento reale (`ttk.Progressbar`) con stima percentuale per ogni fase.
- Pulsante principale **"🚀 Launch Antigravity (Auto-Sync)"** per ciclo completo.
- Pulsante rapido **"⚡ Bypass Sync & Launch Directly"** in caso di emergenza o assenza di rete.
