"""Restricted n8n credential bridge. Never accepts calendar IDs or arbitrary URLs from LLM.
Protocol: server enforces owned events, slot locks, free/busy and operation receipts.
The Google credential remains in n8n. Missing bridge/working-hours => fail closed.
"""

import hashlib
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def label_br(iso):
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(
        ZoneInfo("America/Sao_Paulo")
    )
    days = [
        "segunda-feira",
        "terça-feira",
        "quarta-feira",
        "quinta-feira",
        "sexta-feira",
        "sábado",
        "domingo",
    ]
    months = [
        "janeiro",
        "fevereiro",
        "março",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
    ]
    return f"{days[dt.weekday()]}, {dt.day} de {months[dt.month - 1]} de {dt.year}, às {dt:%Hh%M}"


class TestCalendar:
    """Faithful synthetic calendar: atomic offer/recheck/write, owned events, stable receipts.
    Test slots are EXPLICIT fixtures, not inferred working hours of Ricardo.
    """

    __test__ = False

    def __init__(self, slots):
        self.slots, self.events, self.receipts, self.offered = slots, {}, {}, {}
        self.lock = threading.Lock()
        self.conflict = False

    def call(self, owner, key, name, args):
        with self.lock:
            if key in self.receipts:
                return self.receipts[key]
            result = self.execute(owner, key, name, args)
            self.receipts[key] = result
            return result

    def execute(self, owner, key, name, args):
        if name == "consultar_horarios":
            slots = [s for s in self.slots if self.free(s)]
            self.offered[owner] = slots
            return {
                "slots": [
                    {
                        "inicio": s,
                        "fim": (
                            datetime.fromisoformat(s.replace("Z", "+00:00"))
                            + timedelta(minutes=45)
                        )
                        .isoformat()
                        .replace("+00:00", "Z"),
                        "label_br": label_br(s),
                    }
                    for s in slots
                ]
            }
        email = args.get("email", "").lower()
        owned = [
            v
            for v in self.events.values()
            if v["owner"] == owner and v["email"] == email and not v["cancelled"]
        ]
        if name in ("remarcar", "cancelar") and len(owned) != 1:
            return {"sucesso": False, "erro": "reuniao_nao_localizada_nesta_conversa"}
        if name == "cancelar":
            owned[0]["cancelled"] = True
            return {"sucesso": True}
        slot = args.get("horario", args.get("novo_horario"))
        if slot not in self.offered.get(owner, []):
            return {"sucesso": False, "erro": "consultar_horarios_antes"}
        if not self.free(slot, owned[0]["id"] if owned else None):
            return {"sucesso": False, "erro": "horário indisponível"}
        event_id = (
            owned[0]["id"]
            if name == "remarcar"
            else hashlib.sha256(key.encode()).hexdigest()[:32]
        )
        self.events[event_id] = {
            "id": event_id,
            "owner": owner,
            "email": email,
            "slot": slot,
            "minutes": 45,
            "meet": True,
            "invitation": True,
            "cancelled": False,
        }
        return {
            "sucesso": True,
            "horario_confirmado": slot,
            "label_br": label_br(slot),
            "meet_link": "https://meet.google.com/synthetic",
        }

    def free(self, slot, ignore=None):
        start = datetime.fromisoformat(slot.replace("Z", "+00:00"))
        if self.conflict and slot == self.slots[0]:
            return False
        return all(
            v["id"] == ignore
            or v["cancelled"]
            or abs(
                (
                    datetime.fromisoformat(v["slot"].replace("Z", "+00:00")) - start
                ).total_seconds()
            )
            >= 2700
            for v in self.events.values()
        )
