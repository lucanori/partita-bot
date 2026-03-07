---
status: completed
created_at: 2026-03-06
files_edited: [partita_bot/config.py, partita_bot/storage.py, partita_bot/admin.py, run_bot.py, tests/test_admin.py, tests/test_storage_methods.py, tests/test_run_bot_helpers.py, wsgi.py, tests/test_wsgi.py]
rationale: Introdurre coda admin dedicata con flag di rollout per separare le operazioni di backend dal flusso messaggi utente e migrare via dal sentinel telegram_id=0
supporting_docs: []
---

## Summary of changes
- Aggiunto flag USE_ADMIN_QUEUE (default on) e nuova tabella `admin_queue` con API dedicate in `storage.py` per accodare, leggere e marcare operazioni admin.
- Migrazione automatica: quando la tabella viene creata e il flag è attivo, le operazioni legacy (`telegram_id=0`) vengono migrate nella nuova coda.
- Admin Flask ora usa `enqueue_admin_operation` (rispetta il flag) invece del sentinel; il worker avvia un thread separato per processare la admin queue quando il flag è on, mantenendo il percorso legacy come fallback.
- Aggiornati test per coprire entrambe le modalità (flag on/off) e nuove API di storage; aggiunti test per process_admin_operation con sorgente admin_queue.

## Technical reasoning
- Una coda dedicata evita l’overload semantico della message queue utente e semplifica la migrazione/monitoraggio delle operazioni amministrative.
- Il flag di rollout permette fallback immediato al percorso legacy senza toccare l’admin UI.
- La migrazione è idempotente e avviene all’inizializzazione schema, riducendo rischi su installazioni esistenti.

## Impact assessment
- Separazione netta tra messaggi utente e operazioni admin; ridotto rischio di collisioni o trattamenti speciali sul sentinel.
- Il worker ora gestisce due thread: admin queue (solo se attiva) e message queue; latenza admin rimane bassa grazie al polling dedicato.
- Env nuova: `USE_ADMIN_QUEUE` (default true). In assenza, il sistema continua a usare il sentinel legacy.

## Validation steps
- `ruff check .`
- `pytest --cov=. --cov-report=term`
- `npx markdownlint-cli "**/*.md" --config .markdownlint.json --ignore-path .markdownlintignore --dot --fix`
