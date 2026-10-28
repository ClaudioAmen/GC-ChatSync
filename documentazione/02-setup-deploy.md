# 02 - Setup & Configurazione

## ⚙️ Requisiti di Sistema
1. **Python**: Versione 3.10 o successiva (con modulo `tkinter` abilitato).
2. **Rclone**: Installato e presente nel PATH di sistema.
3. **Google Drive**: Account Google personale su cui configurare il remote `gdrive`.

---

## 🚀 Installazione Locale

### Windows
1. Rclone installabile tramite Winget:
   ```powershell
   winget install Rclone.Rclone
   ```
2. Installazione pacchetto in modalità editable:
   ```powershell
   cd D:\Progetti\GC-ChatSync
   pip install -e .
   ```
3. Verifica:
   ```powershell
   gc-chatsync --version
   ```

---

## ☁️ Configurazione Remoto Google Drive (`rclone config`)
Per abilitare la sincronizzazione, occorre autorizzare rclone con Google Drive:
1. Esegui:
   ```cmd
   rclone config
   ```
2. Segui i passaggi guidati:
   - `n` (New remote) $\rightarrow$ Nome: `gdrive`
   - Tipo di storage: seleziona il numero corrispondente a `drive` (Google Drive)
   - Client ID & Client Secret: lascia vuoto (premi Invio per usare le chiavi predefinite)
   - Scope: `1` (Full access `drive`)
   - Root Folder ID & Service Account: lascia vuoto (premi Invio)
   - Edit advanced config: `n`
   - Use web browser to automatically authenticate: `y`
   - Si aprirà il browser: accedi al tuo account Google e consenti l'accesso a Rclone.
   - Conferma e salva: `y`, poi `q` per uscire.
3. Test di connettività:
   ```cmd
   rclone about gdrive:
   ```

---

## 🖥️ Integrazione Icona Desktop
* **Icona abituale con backup**:
  ```cmd
  gc-chatsync install-shortcut
  ```
* **Icona separata**:
  ```cmd
  gc-chatsync install-shortcut --standalone
  ```
