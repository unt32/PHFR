
```bash
cd PHFR
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-api.txt
.venv\Scripts\python -m uvicorn app.main:app --reload
```


```bash
cd PHFR/web
npm install
npm run dev
```



```bash
cd PHFR
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

```bash
cd PHFR/web
npm run dev:lan
```

## Running tests

```bash
cd PHFR
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

## Backend layout

See `REFACTOR_NOTES.md` for the full architecture writeup. Short version:
`app/core` is framework-free domain logic (graph loading, edge weights,
A*), `app/services` orchestrates it (locking, caching, background load),
and `app/api` is the thin FastAPI/HTTP layer on top. `uvicorn api:app`
still works via the compatibility shim in the root `api.py`.
