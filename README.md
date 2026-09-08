
```bash
cd PHFR
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-api.txt
.venv\Scripts\python -m uvicorn api:app --reload
```


```bash
cd PHFR/web
npm install
npm run dev
```



```bash
cd PHFR
.venv\Scripts\python -m uvicorn api:app --host 0.0.0.0 --port 8000
```

```bash
cd PHFR/web
npm run dev:lan
```