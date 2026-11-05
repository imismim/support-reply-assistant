from fastapi import FastAPI

from app.routers import health, mock_helpdesk, operator, webhooks

app = FastAPI(title="Support Reply Assistant")

app.include_router(health.router)
app.include_router(webhooks.router)
app.include_router(mock_helpdesk.router)
app.include_router(operator.router)
