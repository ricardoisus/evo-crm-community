"""Calendar policy and durable receipts; Google OAuth remains exclusively in n8n.
Availability comes from Calendly (event type of the 45-min diagnosis meeting), which
already applies Ricardo's hours and his connected-calendar conflicts. Writes go to the
Google calendar fixed in the n8n gateway, after a Calendly + Google free/busy recheck.
A singleton Postgres row serializes this agent's checks + writes. Google manual
edits can still race (Google has no conditional free/busy insert); see runbook.
"""

import hashlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import re
import httpx
from sqlalchemy import Table, Column, String, JSON, select
from .store import meta
from .calendar import label_br
from .configuration import ConfigurationUnavailable

locks = Table("calendar_locks", meta, Column("id", String, primary_key=True))
offers = Table(
    "calendar_offers",
    meta,
    Column("owner", String, primary_key=True),
    Column("data", JSON),
)
meetings = Table(
    "calendar_meetings",
    meta,
    Column("id", String, primary_key=True),
    Column("owner", String),
    Column("email", String),
    Column("data", JSON),
)
receipts = Table(
    "calendar_receipts",
    meta,
    Column("key", String, primary_key=True),
    Column("payload", JSON),
    Column("result", JSON),
)


class GoogleGateway:
    """Calls a fixed, private n8n webhook with headerAuth credential. No arbitrary URL from model."""

    def __init__(self, url="", secret="", *, authority=None, calendar_id=None):
        self.authority, self.calendar_id = authority, calendar_id
        self.url, self.secret = url, secret

    def call(self, op, **args):
        if self.authority:
            args = dict(args, **self.authority.data(), calendar_id=self.calendar_id)
        if not self.url or len(self.secret) < 32:
            raise RuntimeError("agenda_indisponivel")
        try:
            r = httpx.post(
                self.url,
                headers={"X-Vetorial-Calendar-Key": self.secret},
                json={"op": op, **args},
                timeout=15,
                follow_redirects=False,
            )
            if r.status_code == 404 and op == "get":
                return None
            if r.status_code not in (200, 201, 204):
                raise RuntimeError("agenda_indisponivel")
            return r.json() if r.content else {}
        except (httpx.HTTPError, ValueError):
            raise RuntimeError("agenda_indisponivel") from None


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class CalendlyAvailability:
    """Read-only Calendly source. Fixed API host and event type; never a URL from the model.
    Any HTTP/parse failure fails closed (no slots offered, no booking)."""

    BASE = "https://api.calendly.com"
    MAX_RANGE = timedelta(days=7) - timedelta(seconds=1)  # Calendly rejects > 7 days

    def __init__(self, token="", event_type="", get=None, now=None, *, authority=None):
        self.authority = authority
        self.token, self.event_type = token, event_type
        self.get = get or httpx.get
        self.now = now or (lambda: datetime.now(timezone.utc))

    def slots(self, lo, hi):
        if self.authority:
            self.authority.check()
        if len(self.token) < 20 or not re.fullmatch(
            re.escape(self.BASE) + r"/event_types/[0-9a-f-]{36}", self.event_type
        ):
            raise RuntimeError("agenda_indisponivel")
        # Calendly only answers for the future; keep a small margin for request latency.
        lo = max(lo, self.now() + timedelta(minutes=1))
        found = set()
        while lo < hi:
            if self.authority:
                self.authority.check()
            end = min(hi, lo + self.MAX_RANGE)
            try:
                r = self.get(
                    self.BASE + "/event_type_available_times",
                    params={
                        "event_type": self.event_type,
                        "start_time": _iso(lo),
                        "end_time": _iso(end),
                    },
                    # Cloudflare answers 403 to default library user agents.
                    headers={
                        "Authorization": "Bearer " + self.token,
                        "User-Agent": "vetorial-agente/1.0",
                        "Accept": "application/json",
                    },
                    timeout=15,
                    follow_redirects=False,
                )
                if r.status_code != 200:
                    raise RuntimeError("agenda_indisponivel")
                for item in r.json()["collection"]:
                    if item.get("status") == "available":
                        found.add(_iso(datetime.fromisoformat(item["start_time"].replace("Z", "+00:00"))))
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
                raise RuntimeError("agenda_indisponivel") from None
            lo = end
        return sorted(found)


class FixedAvailability:
    """Explicit weekly fixtures for tests/evals ([{weekday, start, end}], São Paulo).
    Never a default for Ricardo's real hours."""

    def __init__(self, windows):
        self.windows = windows

    def slots(self, lo, hi):
        tz = ZoneInfo("America/Sao_Paulo")
        starts = []
        day = lo.astimezone(tz).date()
        while datetime.combine(day, datetime.min.time(), tz) < hi:
            for w in self.windows:
                if day.weekday() != w["weekday"]:
                    continue
                start = datetime.fromisoformat(f"{day}T{w['start']}").replace(tzinfo=tz)
                end = datetime.fromisoformat(f"{day}T{w['end']}").replace(tzinfo=tz)
                while start + timedelta(minutes=45) <= end:
                    if lo <= start < hi:
                        starts.append(_iso(start))
                    start += timedelta(minutes=45)
            day += timedelta(days=1)
        return sorted(set(starts))


class Agenda:
    def __init__(self, store, google, availability=None, clock=None, authorize=None, *, snapshot=None, authority=None):
        self.authority = authority
        self.configuration = snapshot.payload if snapshot else None
        self.runtime = self.configuration["runtime"] if snapshot else {}
        self.minutes = self.configuration["integrations"]["booking_minutes"] if snapshot else 45
        self.timezone = self.configuration["behavior"]["timezone"] if snapshot else "America/Sao_Paulo"
        self.store, self.google = store, google
        # Calendly in production; FixedAvailability only in tests. None means closed.
        self.availability = availability
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.authorize = authorize or (lambda owner, key: False)

    def call(self, owner, key, name, args):
        payload = dict(owner=owner, name=name, args=args)
        if self.authority:
            payload["configuration"] = self.authority.data()
        with self.store.engine.begin() as c:
            if self.authority:
                self.authority.check()
            c.execute(
                self.store.insert(locks)
                .values(id="google-vetorial")
                .on_conflict_do_nothing()
            )
            c.execute(
                select(locks).where(locks.c.id == "google-vetorial").with_for_update()
            ).one()
            previous = (
                c.execute(select(receipts).where(receipts.c.key == key))
                .mappings()
                .first()
            )
            if previous:
                if previous["payload"] != payload:
                    raise ValueError("chave_de_agenda_reutilizada")
                return previous["result"]
            if not self.authorize(owner, key):
                return {"sucesso": False, "erro": "estado_ou_politica", "slots": []}
            if self.availability is None and name != "cancelar":
                return {
                    "sucesso": False,
                    "erro": "disponibilidade_nao_configurada",
                    "slots": [],
                }
            # Unexpected/timeouts roll back local tx. Stable remote ID + get on retry reconciles.
            try:
                result = self.execute(c, owner, key, name, args)
            except ConfigurationUnavailable:
                raise
            except RuntimeError:
                return {"sucesso": False, "erro": "agenda_indisponivel", "slots": []}
            c.execute(receipts.insert().values(key=key, payload=payload, result=result))
            return result

    iso = staticmethod(_iso)

    def candidates(self, lo, hi):
        """Real availability in [lo, hi), at least 2h ahead and within the 14-day window."""
        now = self.clock()
        lo = max(lo, now + timedelta(hours=2))
        hi = min(hi, now + timedelta(days=self.runtime.get("agenda_horizon_days", 14) + 1))
        if hi <= lo:
            return []
        return [
            s
            for s in self.availability.slots(lo, hi)
            if datetime.fromisoformat(s.replace("Z", "+00:00")) >= lo
        ]

    def period(self, text):
        text = text.lower().strip()
        tz = ZoneInfo(self.timezone)
        day = (
            self.clock()
            .astimezone(tz)
            .replace(hour=0, minute=0, second=0, microsecond=0)
        )
        if (
            "próxima semana" in text
            or "proxima semana" in text
            or "semana que vem" in text
        ):
            start = day + timedelta(days=7 - day.weekday())
            return start, start + timedelta(days=7)
        if "essa semana" in text or "esta semana" in text:
            return day, day + timedelta(days=7 - day.weekday())
        if "amanhã" in text or "amanha" in text:
            return day + timedelta(days=1), day + timedelta(days=2)
        if "hoje" in text:
            return day, day + timedelta(days=1)
        if text in (
            "próximos dias",
            "proximos dias",
            "14 dias",
            "próximas duas semanas",
        ):
            return day, day + timedelta(days=self.runtime.get("agenda_horizon_days", 14) + 1)
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", text)
        if len(dates) in (1, 2):
            try:
                start = datetime.fromisoformat(dates[0]).replace(tzinfo=tz)
                end = datetime.fromisoformat(dates[-1]).replace(tzinfo=tz) + timedelta(
                    days=1
                )
                return start, end
            except ValueError:
                return None
        return None

    def free(self, slot, exclude=None):
        start = datetime.fromisoformat(slot.replace("Z", "+00:00"))
        end = start + timedelta(minutes=self.minutes)
        # For a reschedule use event listing to exclude ONLY the owned event.
        if exclude:
            d = self.google.call("list", timeMin=self.iso(start), timeMax=self.iso(end))
            if d.get("nextPageToken"):
                raise RuntimeError("agenda_indisponivel")
            return all(
                e.get("id") == exclude
                or e.get("status") == "cancelled"
                or e.get("transparency") == "transparent"
                for e in d.get("items", [])
            )
        d = self.google.call("freebusy", timeMin=self.iso(start), timeMax=self.iso(end))
        calendars = list(d.get("calendars", {}).values())
        if (
            len(calendars) != 1
            or calendars[0].get("errors")
            or "busy" not in calendars[0]
        ):
            raise RuntimeError("agenda_indisponivel")
        return not calendars[0]["busy"]

    def execute(self, c, owner, key, name, args):
        now = self.clock()
        if name == "consultar_horarios":
            # Free-text period narrows the conversational request; offer only real configured slots.
            # Return a bounded real 14-day window with explicit range, never pretend parsed dates.
            bounds = self.period(args["periodo"])
            if bounds is None:
                return {
                    "slots": [],
                    "sucesso": False,
                    "erro": "periodo_nao_suportado_use_esta_ou_proxima_semana",
                }
            # Calendly already excludes Ricardo's busy time; Google is rechecked on booking.
            available = self.candidates(*bounds)[:8]
            data = dict(
                slots=available, expires=(now + timedelta(milliseconds=self.runtime.get("slot_validity_ms", 600000))).timestamp()
            )
            if self.authority:
                data["configuration"] = self.authority.data()
            c.execute(
                self.store.insert(offers)
                .values(owner=owner, data=data)
                .on_conflict_do_update(index_elements=["owner"], set_={"data": data})
            )
            return {
                "slots": [
                    {
                        "inicio": s,
                        "fim": self.iso(
                            datetime.fromisoformat(s.replace("Z", "+00:00"))
                            + timedelta(minutes=self.minutes)
                        ),
                        "label_br": label_br(s),
                    }
                    for s in available
                ],
                "janela_consultada": "Próximos 14 dias, horários livres da agenda de Ricardo; selecione dentro desta janela.",
            }
        email = args["email"].lower()
        owned = list(
            c.execute(
                select(meetings).where(
                    meetings.c.owner == owner, meetings.c.email == email
                )
            ).mappings()
        )
        owned = [e for e in owned if not e["data"].get("cancelled")]
        ident = hashlib.sha256(f"vetorial:{owner}".encode()).hexdigest()[:40]
        remote = self.google.call("get", id=ident)
        if (
            remote
            and remote.get("extendedProperties", {}).get("private", {}).get("owner")
            != owner
        ):
            return {"sucesso": False, "erro": "evento_nao_pertence_a_conversa"}
        if remote and not any(
            a.get("email", "").lower() == email for a in remote.get("attendees", [])
        ):
            return {"sucesso": False, "erro": "email_nao_corresponde_a_reuniao"}
        if name in ("remarcar", "cancelar") and not remote and not owned:
            return {"sucesso": False, "erro": "reuniao_nao_localizada_nesta_conversa"}
        if name == "cancelar":
            if not self.authorize(owner, key):
                return {"sucesso": False, "erro": "estado_ou_politica"}
            if remote and remote.get("status") != "cancelled":
                self.google.call("delete", id=ident)
            c.execute(
                meetings.update()
                .where(meetings.c.id == ident)
                .values(data=dict(owned[0]["data"] if owned else {}, cancelled=True))
            )
            return {"sucesso": True}
        slot = args.get("horario", args.get("novo_horario"))
        # Reconcile after remote commit before any conflict checks (own event is now busy).
        if (
            remote
            and remote.get("start", {}).get("dateTime")
            and datetime.fromisoformat(
                remote["start"]["dateTime"].replace("Z", "+00:00")
            )
            == datetime.fromisoformat(slot.replace("Z", "+00:00"))
            and remote.get("status") != "cancelled"
        ):
            return self.persist(c, ident, owner, email, slot, remote)
        if name == "agendar" and remote and remote.get("status") != "cancelled":
            return {"sucesso": False, "erro": "reuniao_existente_use_remarcar"}
        offer = c.execute(select(offers.c.data).where(offers.c.owner == owner)).scalar()
        if (not offer or offer["expires"] < now.timestamp() or slot not in offer["slots"]
                or (self.authority and offer.get("configuration") != self.authority.data())):
            return {"sucesso": False, "erro": "consultar_horarios_antes"}
        start = datetime.fromisoformat(slot.replace("Z", "+00:00"))
        # Recheck both sources: Calendly (hours + its conflicts) and the target Google calendar.
        if slot not in self.candidates(start, start + timedelta(minutes=self.minutes)):
            return {"sucesso": False, "erro": "horário indisponível"}
        if not self.free(slot, ident if name == "remarcar" else None):
            return {"sucesso": False, "erro": "horário indisponível"}
        if not self.authorize(owner, key):
            return {"sucesso": False, "erro": "estado_ou_politica"}
        body = {
            "status": "confirmed",
            "summary": "Diagnóstico Vetorial",
            "start": {"dateTime": slot, "timeZone": self.timezone},
            "end": {
                "dateTime": self.iso(start + timedelta(minutes=self.minutes)),
                "timeZone": self.timezone,
            },
            "attendees": [{"email": email}],
            "extendedProperties": {"private": {"owner": owner}},
            "conferenceData": {
                "createRequest": {
                    "requestId": ident,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
        }
        if name == "agendar":
            body["id"] = ident
        remote = self.google.call(
            "patch" if name == "remarcar" or remote else "insert", id=ident, body=body
        )
        return self.persist(c, ident, owner, email, slot, remote)

    def persist(self, c, ident, owner, email, slot, remote):
        data = {"slot": slot, "cancelled": False}
        c.execute(
            self.store.insert(meetings)
            .values(id=ident, owner=owner, email=email, data=data)
            .on_conflict_do_update(index_elements=["id"], set_={"data": data})
        )
        meet = remote.get("hangoutLink") or next(
            (
                p.get("uri")
                for p in remote.get("conferenceData", {}).get("entryPoints", [])
                if p.get("entryPointType") == "video"
            ),
            None,
        )
        if (
            not meet
            or remote.get("status") == "cancelled"
            or not remote.get("attendees")
        ):
            # Do not confirm before asynchronous Google Meet creation/invitation is visible.
            raise RuntimeError("agenda_indisponivel")
        return {
            "sucesso": True,
            "horario_confirmado": slot,
            "label_br": label_br(slot),
            "meet_link": meet,
        }
