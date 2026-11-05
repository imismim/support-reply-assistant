web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
worker: arq app.worker.tasks.WorkerSettings
release: alembic upgrade head && python -m scripts.seed_kb
