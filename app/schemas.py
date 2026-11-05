from pydantic import BaseModel, EmailStr


class HelpdeskWebhookPayload(BaseModel):
    event_id: str
    ticket_id: str
    customer_email: EmailStr
    message: str
    reply_url: str


class HelpdeskReplyPayload(BaseModel):
    ticket_id: str
    message: str
