# GEMINI.md — Regole di Progetto: GC-ChatSync

> Questo file definisce le invarianti, i controlli obbligatori e la metodologia di lavoro specifica per GC-ChatSync.
> Integra le regole globali in `~/.gemini/GEMINI.md`.

Questo progetto sincronizza cronologie chat tra due PC tramite Google Drive. Un errore può far perdere dati
all'utente, quindi **il tuo codice va considerato sbagliato finché non è dimostrato il contrario**.
Non dire mai "è tutto a posto", "fatto" o "funziona" senza aver completato la procedura qui sotto.

---

## 1. Invarianti da non violare mai

1. Un pull o un merge fallito **non** deve essere seguito da un push che sovrascrive `conversation_summaries.db` sul cloud.
2. L'indice cloud non va mai sostituito senza prima averlo unito (merge) con quello locale.
3. Un errore di rete, di autenticazione o un JSON corrotto **non** equivale a "il cloud è vuoto". Solo i codici
   di uscita rclone 3 e 4 significano "non esiste".
4. Il manifest si scrive **per ultimo**, dopo i dati. Un push interrotto non deve dichiarare completo uno stato incompleto.
5. Nessun push se Antigravity è ancora in esecuzione o se i database sono bloccati. Si carica sempre lo snapshot, mai il file live.
6. Il primo avvio con cartella Drive inesistente deve funzionare e creare tutto.
7. Ipotesi di design: una sola persona, un dispositivo alla volta. Il caso più pericoloso è il passaggio di consegne
   fallito (chiudo A senza push, lavoro su B, riapro A), non l'uso simultaneo.
8. Mai eliminare dati remoti per una cancellazione locale (si usa `copy --update`, mai `sync`).

---

## 2. Dopo ogni modifica: rileggi il tuo lavoro da revisore ostile

Prima di dichiarare concluso il lavoro, esegui **tutti** questi passi e riporta l'esito di ciascuno.

1. **Rileggi il diff completo** (`git diff`), non solo i file che pensi di aver toccato.
   Cerca righe **rimosse** per errore: import, `from __future__ import annotations`, docstring, controlli, esclusioni.
   Un diff con molte righe cambiate in un file che non dovevi toccare è un campanello d'allarme (a capo, formattazione, riscritture).
2. **Esegui i test davvero** e riporta il numero di test passati e falliti. Non scrivere "i test passano" senza averli lanciati.
   Se non puoi eseguirli, scrivilo esplicitamente.
3. **Controlla la versione minima di Python** dichiarata in `pyproject.toml` (oggi 3.10): importa ogni modulo
   e lancia i test con quella versione o con la più vecchia disponibile, non solo con l'ultima.
4. **Scrivi prima un test che riproduce il bug**, verifica che fallisca, poi correggi e verifica che passi.
   Una correzione senza un test che l'abbia mai fatto fallire non è dimostrata.
5. **Prova gli scenari avversi**, uno per uno, e dì per ciascuno cosa succede nel codice (leggilo, non supporlo):
   - crash o spegnimento a metà sessione (flag sporco, push mancato);
   - nessuna rete / rete lenta / timeout rclone a metà trasferimento;
   - cartella Drive inesistente (primo avvio) e manifest assente o corrotto;
   - schema dei database diverso tra i due PC / versioni diverse di Antigravity;
   - Antigravity già aperto o con processi figli ancora vivi alla chiusura;
   - passaggio di consegne fallito tra i due PC;
   - percorso di dati o cartelle con spazi, caratteri unicode, lettere di unità diverse.
6. **Cerca i fallimenti silenziosi:** `except: pass`, `except Exception` che ignora l'errore, valori di ritorno
   non controllati (`checkpoint_db`, `push`, `pull`), messaggi di successo stampati anche quando l'operazione è stata rimandata.
7. **Controlla che ogni affermazione di README, `EXTRA_INFO.md` e `AI_PROMPT.md` corrisponda al codice.**
   Se la documentazione promette qualcosa che il codice non fa, correggi uno dei due.
8. **Controlla lo stato reale del repository remoto**, non una copia in cache: dopo un push verifica con
   `git ls-remote origin` che il commit sia arrivato.
9. **Non fidarti dei tuoi ricordi:** rileggi il codice che stai per citare invece di descriverlo a memoria.

---

## 3. Come riportare il risultato

Chiudi sempre con questo formato. Se una sezione è vuota, scrivi "nessuno", non saltarla.

- **Verificato** (con comando eseguito e risultato): …
- **Non verificato** (e perché): …
- **Problemi trovati nel mio stesso codice:** …
- **Rischi residui / scenari non coperti:** …
- **Esito:** "pronto" solo se "Non verificato" e "Problemi" sono vuoti; altrimenti "non ancora affidabile", con l'elenco di cosa manca.

Regola finale: se ti accorgi di non aver eseguito un passo di questa lista, dillo. Ammettere un controllo saltato
vale più di un "tutto ok" non dimostrato.
