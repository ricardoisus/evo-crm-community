"""Postgres is authoritative. SQLite is used only by offline tests; no real data."""

import time
import logging
from sqlalchemy import (
    MetaData,
    Table,
    Column,
    String,
    Integer,
    Float,
    Boolean,
    JSON,
    create_engine,
    select,
    and_,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

meta = MetaData()
conversations = Table(
    "conversations",
    meta,
    Column("id", String, primary_key=True),
    Column("version", Integer, nullable=False),
    Column("paused", Boolean, default=False),
    Column("phone", String),
    Column("due", Float),
    Column("last_event", Float),
    Column("done", Integer, default=0),
    Column("analysed", Integer, default=0),
    Column("analysis_attempts", Integer, default=0),
    Column("analysis_lease", Float, default=0),
    Column("attempts", Integer, default=0),
    Column("lease", Float, default=0),
    Column("draft", JSON),
    Column("reason", String),
)
events = Table(
    "events",
    meta,
    Column("id", String, primary_key=True),
    Column("inbox_id", String),
    Column("message_id", String),
    Column("kind", String),
    Column("payload", JSON),
    UniqueConstraint("inbox_id", "message_id", "kind"),
)
operations = Table(
    "operations",
    meta,
    Column("key", String, primary_key=True),
    Column("conversation_id", String),
    Column("kind", String),
    Column("created", Float),
    Column("payload", JSON),
    Column("result", JSON),
)


class Store:
    def __init__(self, url):
        self.engine = create_engine(url, pool_pre_ping=True)

    def migrate(self):
        meta.create_all(
            self.engine
        )  # additive v1; operator runs before service, not on HTTP startup

    def insert(self, table):
        return (
            pg_insert(table)
            if self.engine.dialect.name == "postgresql"
            else sqlite_insert(table)
        )

    def ingest(self, event, policy, now=None):
        now = time.time() if now is None else now
        with self.engine.begin() as c:
            inserted = c.execute(
                self.insert(events)
                .values(
                    id=event["event_id"],
                    inbox_id=event["inbox_id"],
                    message_id=event["message_id"],
                    kind=event["kind"],
                    payload=event,
                )
                .on_conflict_do_nothing()
            ).rowcount
            if not inserted:
                return False
            if event["inbox_id"] != policy.inbox_id or event["kind"] in ("group", "status"):
                return False
            ident = event["conversation_id"]
            c.execute(
                self.insert(conversations)
                .values(
                    id=ident,
                    version=0,
                    paused=False,
                    phone=event["phone"],
                    due=now,
                    last_event=now,
                    done=0,
                    analysed=0,
                    lease=0,
                )
                .on_conflict_do_nothing()
            )
            row = (
                c.execute(
                    select(conversations)
                    .where(conversations.c.id == ident)
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            if event["version"] <= row["version"]:
                return False
            allowed = policy.allows(event["phone"]) and event["kind"] not in (
                "group",
                "status",
            )
            if not allowed:
                logging.getLogger("vetorial_agent.policy").warning(
                    "vetorial_agent event=ignored reason=outside_allowlist"
                )
            paused = event["paused"] or event["kind"] == "human" or not allowed
            # A normal incoming event cannot clear an existing human pause.
            if row["paused"] and event["kind"] != "resume":
                paused = True
            c.execute(
                conversations.update()
                .where(conversations.c.id == ident)
                .values(
                    version=event["version"],
                    paused=paused,
                    phone=event["phone"],
                    due=now + (30 if event.get("audio") else 20),
                    last_event=now,
                    lease=0,
                    attempts=0,
                    analysis_attempts=0,
                    analysis_lease=0,
                    draft=None,
                    reason=None if not paused else event["kind"],
                )
            )
            return True

    def get(self, ident):
        with self.engine.connect() as c:
            row = (
                c.execute(select(conversations).where(conversations.c.id == ident))
                .mappings()
                .first()
            )
            return dict(row) if row else None

    def current(self, ident, version):
        row = self.get(ident)
        return row is not None and row["version"] == version and not row["paused"]

    def update(self, ident, **values):
        with self.engine.begin() as c:
            c.execute(
                conversations.update()
                .where(conversations.c.id == ident)
                .values(**values)
            )

    def claim(self, now=None):
        now = time.time() if now is None else now
        with self.engine.begin() as c:
            row = (
                c.execute(
                    select(conversations)
                    .where(
                        and_(
                            conversations.c.paused == False,
                            conversations.c.lease < now,
                            conversations.c.due <= now,
                            conversations.c.done < conversations.c.version,
                        )
                    )
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if row:
                c.execute(
                    conversations.update()
                    .where(conversations.c.id == row["id"])
                    .values(lease=now + 900, attempts=conversations.c.attempts + 1)
                )
                return dict(row, attempts=row["attempts"] + 1)

    def analyse_due(self, now=None):
        now = time.time() if now is None else now
        with self.engine.begin() as c:
            row = (
                c.execute(
                    select(conversations)
                    .where(
                        and_(
                            conversations.c.analysed < conversations.c.version,
                            conversations.c.analysis_lease < now,
                            conversations.c.analysis_attempts < 3,
                            conversations.c.last_event <= now - 300,
                        )
                    )
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if not row:
                return []
            c.execute(
                conversations.update()
                .where(conversations.c.id == row["id"])
                .values(
                    analysis_lease=now + 900,
                    analysis_attempts=row["analysis_attempts"] + 1,
                )
            )
            return [dict(row)]

    def operation(self, key, conversation_id, kind, payload, invoke, now=None):
        """Stable keys are passed through to BOTH CRM and calendar. Their receipts close the crash gap."""
        with self.engine.begin() as c:
            c.execute(
                self.insert(operations)
                .values(
                    key=key,
                    conversation_id=conversation_id,
                    kind=kind,
                    payload=payload,
                    created=time.time() if now is None else now,
                )
                .on_conflict_do_nothing()
            )
            row = (
                c.execute(select(operations).where(operations.c.key == key))
                .mappings()
                .one()
            )
            if row["conversation_id"] != conversation_id or row["payload"] != payload:
                raise ValueError("idempotency_collision")
            if row["result"] is not None:
                return row["result"]
        result = invoke(
            key
        )  # Remote must dedup key atomically; retry after timeout uses same key.
        with self.engine.begin() as c:
            c.execute(
                operations.update().where(operations.c.key == key).values(result=result)
            )
        return result

    def quota(self, ident, kinds, maximum, now=None):
        now = time.time() if now is None else now
        with self.engine.connect() as c:
            rows = c.execute(
                select(operations.c.key).where(
                    and_(
                        operations.c.conversation_id == ident,
                        operations.c.kind.in_(kinds),
                        operations.c.created > now - 3600,
                    )
                )
            ).all()
            return len(rows) < maximum

    def finish(self, ident, version, **values):
        # CAS prevents an old worker from clearing a newer conversation's draft/pause/lease.
        with self.engine.begin() as c:
            c.execute(
                conversations.update()
                .where(
                    and_(
                        conversations.c.id == ident, conversations.c.version == version
                    )
                )
                .values(**values)
            )

    def result(self, key):
        with self.engine.connect() as c:
            row = c.execute(
                select(operations.c.result).where(operations.c.key == key)
            ).first()
            return row[0] if row else None

    def quota_key(self, ident, kinds, maximum, key, now):
        # A retry of a persisted operation must not consume a second quota unit.
        with self.engine.connect() as c:
            if c.execute(
                select(operations.c.key).where(operations.c.key == key)
            ).first():
                return True
        return self.quota(ident, kinds, maximum, now)
