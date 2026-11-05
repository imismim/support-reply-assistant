import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TicketStatus(str, enum.Enum):
    received = "received"
    drafting = "drafting"
    pending_review = "pending_review"
    approved = "approved"
    rejected = "rejected"
    sent = "sent"
    failed = "failed"


class WebhookEvent(Base):
    """Every helpdesk webhook delivery, keyed by event_id for dedup.

    The unique primary key on event_id is what makes duplicate/concurrent
    deliveries of the same event safe: a second INSERT for the same
    event_id is rejected atomically by Postgres, regardless of timing.
    """

    __tablename__ = "webhook_events"

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    ticket_id: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    customer_email: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(Text)
    reply_url: Mapped[str] = mapped_column(String)
    status: Mapped[TicketStatus] = mapped_column(
        SAEnum(TicketStatus, name="ticket_status"), default=TicketStatus.received
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    draft: Mapped["Draft | None"] = relationship(back_populates="ticket", uselist=False)
    outbound_reply: Mapped["OutboundReply | None"] = relationship(back_populates="ticket", uselist=False)


class KBArticle(Base):
    __tablename__ = "kb_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), unique=True)
    content: Mapped[str] = mapped_column(Text)
    cited_article_ids: Mapped[list[int]] = mapped_column(JSONB, default=list)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket: Mapped["Ticket"] = relationship(back_populates="draft")


class OutboundReply(Base):
    """Delivery state for a ticket's approved reply.

    The unique constraint on ticket_id lets the approve endpoint use the same
    insert-if-new idempotency pattern as the webhook: only the first Approve
    click creates the row and enqueues delivery, so double-clicks or retried
    requests never schedule a second send.
    """

    __tablename__ = "outbound_replies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), unique=True)
    status: Mapped[str] = mapped_column(String, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    ticket: Mapped["Ticket"] = relationship(back_populates="outbound_reply")
