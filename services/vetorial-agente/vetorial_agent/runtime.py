"""Durable worker. Every side effect has a stable receipt, every block a fresh CRM fence.
Only one worker per deployment in v1. Postgres claims also serialize concurrent processes.
"""

import hashlib
import json
import time
import re
from .adapters import validate_action, validate_output


class Stale(Exception):
    pass


class PendingReceipt(Exception):
    pass


class Worker:
    def __init__(
        self, store, crm, model, calendar, policy, sleep=time.sleep, clock=time.time
    ):
        self.store, self.crm, self.model, self.calendar, self.policy = (
            store,
            crm,
            model,
            calendar,
            policy,
        )
        self.sleep, self.clock = sleep, clock

    def guard(self, ident, version, analysis=False):
        row = self.store.get(ident)
        if row is None or row["version"] != version or (row["paused"] and not analysis):
            raise Stale()
        state = self.crm.request(ident)
        if state["version"] != version or (
            not state.get("eligible", state["allowed"])
            if analysis
            else state["paused"] or not state["allowed"]
        ):
            raise Stale()
        return state

    def command(self, row, suffix, kind, **payload):
        self.guard(row["id"], row["version"], analysis=kind == "analysis")
        key = f"{row['id']}:{row['version']}:{suffix}"
        data = dict(kind=kind, **payload)
        return self.store.operation(
            key,
            row["id"],
            kind,
            data,
            lambda k: self.crm.command(row["id"], row["version"], k, kind, **payload),
            now=self.clock(),
        )

    def context(self, row, analysis=False):
        self.guard(row["id"], row["version"], analysis=analysis)
        context = self.crm.history(row["id"])
        if context["version"] != row["version"] or (
            not context.get("eligible", context["allowed"])
            if analysis
            else context["paused"] or not context["allowed"]
        ):
            raise Stale()
        media_count = 0
        for message in context["messages"]:
            for attachment in message.get("attachments", []):
                self.guard(row["id"], row["version"], analysis=analysis)
                media_count += 1
                if media_count > 10 or attachment.get("bytes", 0) > 20 * 1024 * 1024:
                    message["media_unavailable"] = True
                    continue
                try:
                    key = f"{row['id']}:media:{attachment['id']}"

                    def extract(_):
                        d = self.crm.command(
                            row["id"],
                            row["version"],
                            f"{row['id']}:{row['version']}:{key}",
                            "media",
                            attachment_id=attachment["id"],
                        )
                        return {"text": self.model.media(d["mime"], d["base64"])}

                    result = self.store.operation(
                        key,
                        row["id"],
                        "media",
                        {"id": attachment["id"]},
                        extract,
                        now=self.clock(),
                    )
                    message.setdefault("media_text", []).append(result["text"])
                except (ValueError, RuntimeError):
                    message["media_unavailable"] = True
        return context

    def action(self, row, name, args):
        args = validate_action(name, args)
        self.guard(row["id"], row["version"])
        if name == "atualizar_negocio":
            digest = hashlib.sha256(
                json.dumps(args, sort_keys=True).encode()
            ).hexdigest()[:20]
            return self.command(row, "fields:" + digest, "fields", fields=args)
        if name == "transferir_para_ricardo":
            # Defer permanent pause until the final response. CRM handoff then blocks all new output.
            row["handoff"] = args["motivo"]
            return {"sucesso": True, "estado": "transferencia_apos_resposta"}
        if name not in ("consultar_horarios", "agendar", "remarcar", "cancelar"):
            raise ValueError("acao_invalida")
        mutable = name != "consultar_horarios"
        # Runtime derives consent from latest trusted CRM history, not LLM claims.
        if mutable:
            ctx = self.crm.history(row["id"])
            if not consent(ctx, name, args):
                return {"sucesso": False, "erro": "consentimento_de_agenda_ausente"}
        payload = dict(name=name, args=args)
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()[:24]
        key = f"{row['id']}:{row['version']}:agenda:{digest}"
        if mutable and not self.store.quota_key(
            row["id"], ["agendar", "remarcar", "cancelar"], 3, key, self.clock()
        ):
            return {"sucesso": False, "erro": "limite_de_agenda"}
        # Calendar's durable receipts/Google event ID close timeout-after-commit gap.
        result = self.store.operation(
            key,
            row["id"],
            name,
            payload,
            lambda k: self.calendar.call(row["id"], k, name, args),
            now=self.clock(),
        )
        self.guard(row["id"], row["version"])
        return result

    def tick(self):
        row = self.store.claim(self.clock())
        if not row:
            return False
        ident, version = row["id"], row["version"]
        try:
            if not self.policy.allows(row["phone"]):
                self.store.finish(ident, version, paused=True, reason="politica")
                return True
            draft = row.get("draft")
            if not draft or draft.get("version") != version:
                output = self.model.run(
                    self.context(row), lambda n, a: self.action(row, n, a)
                )
                validate_output(output)
                self.guard(ident, version)
                draft = {
                    "version": version,
                    "output": output,
                    "handoff": row.get("handoff"),
                }
                self.store.finish(ident, version, draft=draft)
            output = draft["output"]
            for index, block in enumerate(output["mensagens"]):
                suffix = f"block:{index}"
                key = f"{ident}:{version}:{suffix}"
                if self.store.result(key) is not None:
                    # Accepted previously: poll its durable transport receipt, never regenerate it.
                    receipt = self.crm.command(ident, version, key, "send", text=block)
                    if receipt.get("status") != "sent":
                        raise PendingReceipt()
                    continue
                if not self.store.quota_key(ident, ["send"], 20, key, self.clock()):
                    self.command(
                        row,
                        "limit",
                        "handoff",
                        note="Limite de mensagens por hora atingido.",
                        labels=["pediu-humano"],
                    )
                    raise Stale()
                self.command(row, f"typing:{index}", "typing")
                # Deterministic 1500 chars/min, bounded; interrupts checked every 0.5 sec.
                remaining = min(12, max(0.5, len(block) * 60 / 1500))
                while remaining > 0:
                    self.sleep(min(0.5, remaining))
                    remaining -= 0.5
                    self.guard(ident, version)
                receipt = self.command(row, suffix, "send", text=block)
                if receipt.get("status") != "sent":
                    raise PendingReceipt()
            if draft.get("handoff"):
                self.command(
                    row,
                    "handoff",
                    "handoff",
                    note=draft["handoff"],
                    labels=["pediu-humano"],
                )
            elif output["acao"] == "encerrar_silencioso":
                self.command(
                    row,
                    "silent",
                    "silent",
                    note=output["motivo_interno"],
                    labels=["fora-de-escopo"],
                )
            self.store.finish(
                ident, version, done=version, lease=0, last_event=self.clock()
            )
        except PendingReceipt:
            self.store.finish(
                ident,
                version,
                lease=self.clock() + 2,
                attempts=max(0, row["attempts"] - 1),
            )
        except Stale:
            self.store.finish(ident, version, draft=None, lease=self.clock() + 2)
        except Exception:
            # No exception payloads: provider errors can contain PII/headers.
            if row["attempts"] >= 3:
                try:
                    self.command(
                        row,
                        "runtime-error",
                        "handoff",
                        note="Três tentativas sem conclusão; revisão humana necessária.",
                        labels=["pediu-humano"],
                    )
                except Exception:
                    pass
                self.store.finish(
                    ident, version, paused=True, lease=0, reason="limite_de_tentativas"
                )
            else:
                self.store.finish(
                    ident, version, lease=self.clock() + 15, reason="tentativa_pendente"
                )
        return True

    def analyse(self):
        for row in self.store.analyse_due(self.clock()):
            try:
                analysis = self.model.run(
                    self.context(row, analysis=True), None, analyst=True
                )
                self.guard(row["id"], row["version"], analysis=True)
                # Commercial rules only authorize suggestions. SQL is always human.
                fields = analysis.get("campos_atualizados", {})
                note = "\n".join(
                    x
                    for x in [
                        analysis.get("resumo"),
                        analysis.get("nota_privada"),
                        "Etapa sugerida: " + str(analysis.get("etapa_sugerida")),
                        analysis.get("justificativa"),
                    ]
                    if x
                )
                self.command(
                    row,
                    "analysis",
                    "analysis",
                    fields=fields,
                    labels=analysis_labels(analysis),
                    note=note,
                )
                self.store.finish(
                    row["id"], row["version"], analysed=row["version"], analysis_lease=0
                )
            except Exception:
                self.store.finish(
                    row["id"],
                    row["version"],
                    analysis_lease=self.clock() + 15,
                    reason="analise_pendente",
                )
                continue


def consent(context, action, args):
    """Conservative extra fence: latest user turn must affirm the requested mutation.
    Provider ownership is still the authority; this never authorizes another contact's event.
    """
    messages = context["messages"]
    users = [m.get("content", "") for m in messages if m["role"] == "user"]
    latest = users[-1].lower() if users else ""
    # Email must have appeared in the real user history, not only assistant/tool text.
    if args.get("email", "").lower() not in " ".join(users).lower():
        return False
    recent = " ".join(m.get("content", "") for m in messages[-6:]).lower()
    affirmative = bool(
        re.search(
            r"\b(sim|confirmo|confirmado|correto|certo|pode|quero|isso|combinado|ok)\b",
            latest,
        )
    )
    if action == "cancelar":
        previous = next(
            (
                m.get("content", "").lower()
                for m in reversed(messages[:-1])
                if m["role"] == "assistant"
            ),
            "",
        )
        return (
            affirmative
            and "cancel" in previous
            and ("certeza" in previous or "confirm" in previous)
        )
    if action == "remarcar":
        return affirmative and bool(re.search(r"remarc|trocar|mudar", recent))
    return (
        affirmative
        and ("@" in recent)
        and bool(re.search(r"reunião|reuniao|convite|agend|horário|horario", recent))
    )


def analysis_labels(analysis):
    labels = list(analysis.get("etiquetas", []))
    mql = analysis.get("mql") or {}
    criteria = mql.get("criterios") or {}
    if mql.get("status") != "mql" or any(
        criteria.get(k) != "elegivel"
        for k in ("profissao", "atendimento_particular", "contexto_clinico")
    ):
        labels = [l for l in labels if l != "mql-declarado"]
    # Meeting label requires a durable calendar receipt, never only analyst inference.
    return [l for l in labels if l != "reuniao-agendada"]
