# 01 - Panoramica del Progetto GC-ChatSync

## 🎯 Scopo & Visione
`GC-ChatSync` nasce dall'esigenza reale di utilizzare Google Antigravity su più postazioni di lavoro (in particolare postazione fissa Windows e laptop Linux) mantenendo costantemente sincronizzati:
1. Storico delle chat e sessioni (`~/.gemini/antigravity/conversations/`).
2. Indice globale delle conversazioni (`~/.gemini/antigravity/conversation_summaries.db`).
3. Artefatti di piano, diff e scratchpad (`~/.gemini/antigravity/brain/`).
4. Definizioni dei progetti workspace (`~/.gemini/config/projects/*.json`).

Il tutto salvato sul proprio spazio privato Google Drive in totale sicurezza, senza server proprietari intermedi o costi di abbonamento.

---

## 🛠️ Stack Tecnologico
* **Linguaggio**: Python 3.10+ (standard library only per massima portabilità).
* **Motore di Trasferimento Cloud**: `rclone` (Google Drive provider, non-destructive `copy --update`).
* **Interfaccia Grafica**: Python Tkinter / ttk con auto-rilevamento tema Dark/Light del sistema operativo.
* **Database & Concorrenza**: SQLite3 con snapshot transazionali, `PRAGMA wal_checkpoint(TRUNCATE)` e lock esclusivi non-bloccanti.

---

## 🏗️ Architettura dei Moduli (`src/gc_chatsync/`)
* **`cli.py`**: Interfaccia da riga di comando per i comandi `run`, `launch`, `pull`, `push`, `status`, `setup`, `gui`, `install-shortcut`, `restore-shortcut`.
* **`config.py`**: Gestione della configurazione (`config.json`), rilevamento automatico percorsi OS e localizzazione dell'eseguibile Antigravity.
* **`desktop.py`**: Creazione e patching delle shortcut di sistema (.lnk su Windows, .desktop su Linux) per collegare l'icona abituale di Antigravity a GC-ChatSync.
* **`drive.py`**: Wrapper ottimizzato attorno a `rclone` per il download e upload sicuro verso Google Drive con gestione manifest degli epoch.
* **`gui.py`**: Dashboard grafica con barra di avanzamento (`ttk.Progressbar`), log in tempo reale e opzione di bypass sync rapido.
* **`lock.py`**: Meccanismo di lock di processo inter-device con rilevamento crash e flag di dirty session.
* **`merger.py`**: Engine di merge bidirezionale a due vie per `conversation_summaries.db` tramite `ATTACH DATABASE` e salvataggio automatico di backup storici.
* **`project_mapper.py`**: Traduttore cross-platform bidirezionale di URI workspace tra Windows (`file:///C:/Users/...`) e Linux (`file:///home/...`).
* **`runner.py`**: Orchestratore del ciclo di vita: PRE-CHECK -> PULL -> RUN APP -> POST-SYNC PUSH.
* **`theme.py`**: Gestione temi grafici adattivi (dark mode nativa Windows/Linux).
