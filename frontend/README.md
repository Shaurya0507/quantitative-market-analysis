# MarketLens dashboard

From the project root, start the FastAPI server with
`.venv/bin/uvicorn backend.app.main:app --reload`. In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open the local Vite URL, normally `http://localhost:5173`. The dashboard uses
the API at `http://localhost:8000`; set `VITE_API_URL` in `.env.local` to
override it.
