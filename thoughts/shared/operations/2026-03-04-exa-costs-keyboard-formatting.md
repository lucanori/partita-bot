---
status: completed
created_at: 2026-03-04
files_edited:
  - partita_bot/bot.py
  - partita_bot/event_fetcher.py
  - partita_bot/storage.py
  - partita_bot/admin.py
  - templates/admin.html
  - thoughts/shared/status/2026-03-04-preexisting-bot-keyboard-change.md
rationale: "Formattazione città più leggibile, tastiera sempre visibile, tracciamento costi Exa esposto in admin"
supporting_docs: []
---

## Summary of changes

- Formattazione del messaggio di conferma città: ogni città ora è su una riga separata e il testo mantiene la finestra oraria e fuso orario.
- Migliorata la persistenza della tastiera "🏙 Imposta città" aggiungendo un handler generale per i messaggi testuali fuori dalla conversazione.
- Tracciamento dei costi Exa: cattura di `costDollars.total` per Answer, Search e classificazione città, persistenza in un nuovo modello `exa_costs` e visualizzazione del totale nell'admin panel.

## Technical reasoning

- Usare `"\n".join(...)` per le città mantiene l'ordine e offre leggibilità senza alterare la logica di salvataggio.
- Un handler `handle_general_message` intercetta i testi non gestiti e ripristina la tastiera per evitare di dover usare `/start`.
- I costi Exa vengono registrati in microdollari per evitare problemi di floating point; `_record_cost_from_response` centralizza l'estrazione e `Database` espone aggregazioni e migrazione tramite `_upgrade_schema`.

## Impact assessment

- UX migliorata nella conferma città e nella disponibilità dei pulsanti.
- Admin ora vede il totale cumulativo dei costi Exa; nessun impatto sulle funzionalità di notifica.
- Nuova tabella `exa_costs` richiede migrazione automatica all'avvio.

## Validation steps

- `ruff check .`
- `pytest --cov=. --cov-report=term`
