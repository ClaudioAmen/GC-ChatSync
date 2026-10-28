# 📚 Documentazione GC-ChatSync

> **Indice generale e mappa di navigazione tecnica per il progetto GC-ChatSync.**

---

## 🗺️ Mappa della Documentazione

| Documento | Descrizione |
| :--- | :--- |
| [**01-panoramica.md**](01-panoramica.md) | Visione, architettura dei moduli, lifecycle e stack tecnologico |
| [**02-setup-deploy.md**](02-setup-deploy.md) | Installazione dipendenze, setup rclone Google Drive, shortcut e comandi |
| [**03-funzionalita.md**](03-funzionalita.md) | Funzionalità CLI & GUI, meccanismo di merge SQLite, lock e traduzione path |
| [**04-decisioni.md**](04-decisioni.md) | Registro decisioni architetturali (ADR) e scelte tecniche salienti |
| [**05-problemi-noti.md**](05-problemi-noti.md) | Limitazioni note, recovery da crash e roadmap |
| [**changelog/**](changelog/) | Storico avanzamenti settimanali (es. [2026-W44.md](changelog/2026-W44.md)) |

---

## 📊 Stato Attuale del Progetto
* **Versione**: `0.1.0`
* **Stato**: In fase di onboarding iniziale e configurazione cloud Google Drive.
* **Componenti Pronti**: CLI, GUI Tkinter multi-thread, Engine di Merge SQLite 2-way, Modulo Desktop shortcut, Suite di Test (60/60 passati).
* **Prossimo Passo**: Configurazione remota `gdrive` tramite `rclone config` per l'autorizzazione OAuth di Google Drive.
