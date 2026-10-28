# Review di GC-ChatSync

Repo: https://github.com/ClaudioAmen/GC-ChatSync

> **Nota sul perimetro:** la review si basa solo sul README. GitHub ha bloccato l'accesso automatico alle cartelle `src/`, `scripts/` e `tests/`, quindi il codice non è stato analizzato. Quanto segue riguarda design e documentazione.

---

## Cosa vuoi fare

GC-ChatSync è un wrapper Python attorno a `rclone` che sincronizza su Google Drive la cronologia chat di Google Antigravity tra un PC Windows e un laptop Linux.

- Fa un **pull** prima di avviare Antigravity e un **push** dopo la chiusura.
- Unisce via SQL l'indice `conversation_summaries.db`, così nessuna sessione viene persa.
- Usa un `manifest.json` su Drive per sapere quale macchina ha sincronizzato per ultima.

L'idea è chiara e il problema è reale.

---

## Lacune e falle nel design

### 1. Il manifest con un solo `last_pc` è fragile

Regge solo con due macchine e uso strettamente alternato.

- Se lavori su A, poi su B senza che A abbia fatto push (crash, chiusura forzata, batteria), B vede un cloud "vecchio" e A al prossimo avvio pensa di essere aggiornato. Si creano due storie divergenti.
- **Suggerimento:** un manifest con timestamp o hash per macchina, oppure un contatore di versione, al posto di un singolo "ultimo PC".

### 2. Il push dipende dalla chiusura "pulita" di Antigravity

- Se l'app crasha, il PC si spegne o chiudi il terminale del wrapper, il PUSH non parte. Serve gestire segnali, `atexit` e un controllo "push pendente" al prossimo avvio.
- Se Antigravity si rilancia da un processo figlio o si stacca (comune con app Electron), il wrapper potrebbe credere che sia chiusa mentre è ancora aperta. Va verificato come viene rilevata la fine.

### 3. Il merge copre solo l'indice, non i singoli `<uuid>.db`

- Se la stessa conversazione viene continuata su entrambe le macchine, quale versione vince? Un `INSERT OR REPLACE` sull'indice può far perdere il riassunto più recente se non si confronta un `updated_at`.
- Il README non dice cosa succede con `conversations/` e `brain/` in caso di conflitto. Con `rclone sync` si rischia di cancellare file dell'altra macchina; con `copy` o `copyto --update` no, ma la scelta va fatta in modo esplicito.

### 4. Checkpoint WAL

Il README parla di "checkpoint local SQLite databases" prima del push.

- Dovrebbe essere `PRAGMA wal_checkpoint(TRUNCATE)`, controllando il valore di ritorno.
- Se un altro processo tiene ancora un lock, il checkpoint può essere parziale e si copia un `.db` incompleto.
- **Suggerimento:** usare `sqlite3 .backup` o `VACUUM INTO` su una copia, e caricare quella.

### 5. Traduzione dei path (`C:\...` ↔ `/home/...`)

È la parte più fragile. Servono:

- una mappa configurabile per progetto;
- gestione di drive letter diverse, path con spazi e caratteri unicode;
- un comportamento chiaro quando il progetto non esiste sull'altra macchina.

### 6. Sicurezza e privacy

- Le chat di sviluppo contengono codice e, a volte, segreti incollati per sbaglio, token e path interni. Finché la cifratura AES-256 resta in roadmap, stanno su Drive in chiaro.
  - **Suggerimento:** anticipare almeno la cifratura con `rclone crypt`, già disponibile e senza dover scrivere crittografia propria.
- Il token OAuth di rclone sta in `rclone.conf` in chiaro.
  - **Suggerimento:** dirlo nel README, consigliare permessi `600` e usare uno scope ristretto (`drive.file` o `--drive-root-folder-id`) su una cartella dedicata.

### 7. Dipendenza da formati interni non documentati

Schema di `conversation_summaries.db`, percorso `~/.gemini/antigravity/` e struttura dei `.db` possono cambiare a ogni aggiornamento di Antigravity. Servono:

- un controllo di versione dello schema prima del merge;
- un backup automatico prima di ogni merge;
- il rifiuto di procedere se lo schema non coincide.

### 8. Backup e rollback

Non risulta un backup locale prima di pull/merge. Se il merge è sbagliato, si perde la cronologia. Una cartella `.gc-chatsync-backup/` con gli ultimi N snapshot costa poco e salva.

### 9. Concorrenza

Se si lancia `gc-chatsync run` due volte, o `pull` mentre Antigravity è aperta, non c'è un lock locale. Un lockfile (`fcntl` su Linux, `msvcrt` su Windows) risolverebbe.

### 10. Clock skew

Se si usano timestamp per decidere quale versione è più recente, orologi sfasati tra i due PC danno risultati sbagliati. Meglio contatori monotoni o hash.

---

## Cose minori

- Il nome "GC" è ambiguo (Google Cloud? Google Chat?). Il sottotitolo spiega, ma il nome da solo no.
- Il README dichiara "✅ Supported" per Linux e Windows mentre la roadmap ha ancora voci aperte: verificare che le spunte rispecchino lo stato reale.
- "100% free" e "zero overhead" sono promesse forti: Drive ha quote di API e di spazio, e `brain/` può crescere parecchio.
- Aggiungere una sezione **Limitazioni note** e una di **Troubleshooting** aumenta la fiducia di chi prova il tool.
- Verificare che i test coprano proprio i casi di conflitto e di crash descritti sopra, non solo il percorso felice.

---

## Prossimo passo suggerito

Una review del codice, partendo dal merge SQL e dal runner, perché è lì che si concentrano i rischi di perdita dati.
