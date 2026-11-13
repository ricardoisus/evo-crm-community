"""Durable worker. Every side effect has a stable receipt, every block a fresh CRM fence.
Only one worker per deployment in v1. Postgres claims also serialize concurrent processes.
"""

import hashlib
import json
import time
import re
from .adapters import validate_action, validate_output
from .configuration import ConfigurationUnavailable


FIRST_BLOCK_TYPING_CAP_SECONDS = 6


class Stale(Exception):
    pass


class PendingReceipt(Exception):
    pass


class Worker:
    def __init__(
        self, store, crm, model, calendar, policy, sleep=time.sleep, clock=time.time,
        configuration_cache=None, configure=None
    ):
        self.store, self.crm, self.model, self.calendar, self.policy = (
            store,
            crm,
            model,
            calendar,
            policy,
        )
        self.sleep, self.clock = sleep, clock
        self.configuration_cache, self.configure = configuration_cache, configure
        self.snapshot = None
        self.authority = None

    def prepare(self):
        """Pin before claiming work: an unavailable configuration leaves queues untouched.

        configure constructs adapters from the pinned payload, never from legacy env.
        The snapshot is kept for the entire execution, including all tool calls.
        """
        if self.configuration_cache is None:
            return True  # A14 compatibility path, unchanged.
        try:
            snapshot = self.configuration_cache.pin()
            model, calendar, policy = self.configure(snapshot)
        except ConfigurationUnavailable:
            return False
        from .configuration import EffectFence
        self.authority = EffectFence(snapshot, self.configuration_cache)
        self.snapshot = snapshot
        self.model, self.calendar, self.policy = model, calendar, policy
        return True

    def setting(self, section, name, legacy):
        return self.snapshot.payload[section][name] if self.snapshot else legacy

    def check_configuration(self):
        if self.configuration_cache is None:
            return
        current = self.configuration_cache.pin()
        if self.snapshot is None or current.fence() != self.snapshot.fence():
            raise Stale()


    def guard(self, ident, version, analysis=False):
        self.check_configuration()
        row = self.store.get(ident)
        if row is None or row["version"] != version or (row["paused"] and not analysis):
            raise Stale()
        if analysis and not self.policy.allows_analysis(row["phone"]):
            raise Stale()
        state = self.crm.request(ident)
        if state["version"] != version or (
            not state.get("analysis_allowed", state.get("eligible", state["allowed"]))
            if analysis
            else state["paused"] or not state["allowed"]
        ):
            raise Stale()
        return state

    def command(self, row, suffix, kind, **payload):
        self.guard(row["id"], row["version"], analysis=kind == "analysis")
        key = f"{row['id']}:{row['version']}:{suffix}"
        if self.authority:
            payload = dict(payload, **self.authority.data())
        data = dict(kind=kind, **payload)
        return self.store.operation(
            key,
            row["id"],
            kind,
            data,
            lambda k: self.crm.command(row["id"], row["version"], k, kind, **payload),
            now=self.clock(), authority=self.authority,
        )

    def context(self, row, analysis=False):
        self.guard(row["id"], row["version"], analysis=analysis)
        context = self.crm.history(row["id"])
        if context["version"] != row["version"] or (
            not context.get("analysis_allowed", context.get("eligible", context["allowed"]))
            if analysis
            else context["paused"] or not context["allowed"]
        ):
            raise Stale()
        if len(context["messages"]) > self.setting("runtime", "history_messages", 1000):
            raise ValueError("historico_excede_limite")
        media_count = 0
        for message in context["messages"]:
            for attachment in message.get("attachments", []):
                self.guard(row["id"], row["version"], analysis=analysis)
                media_count += 1
                if media_count > self.setting("runtime", "media_items", 10) or attachment.get("bytes", 0) > self.setting("runtime", "attachment_bytes", 20 * 1024 * 1024):
                    message["media_unavailable"] = True
                    continue
                try:
                    key = f"{row['id']}:media:{attachment['id']}"
                    if self.snapshot:
                        key += ":" + self.snapshot.sha256

                    def extract(_):
                        d = self.crm.command(
                            row["id"],
                            row["version"],
                            f"{row['id']}:{row['version']}:{key}",
                            "analysis_media" if analysis else "media",
                            attachment_id=attachment["id"],
                            **(self.authority.data() if self.authority else {}),
                        )
                        return {"text": self.model.media(d["mime"], d["base64"])}

                    result = self.store.operation(
                        key,
                        row["id"],
                        "media",
                        {"id": attachment["id"]},
                        extract,
                        now=self.clock(), authority=self.authority,
                    )
                    message.setdefault("media_text", []).append(result["text"])
                except ConfigurationUnavailable:
                    raise
                except (ValueError, RuntimeError):
                    message["media_unavailable"] = True
        return context

    def action(self, row, name, args):
        contract = self.snapshot.payload["roles"]["atendimento"]["actions"] if self.snapshot else None
        args = validate_action(name, args, contract)
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
            row["id"], ["agendar", "remarcar", "cancelar"], self.setting("runtime", "agenda_mutations_per_hour", 3), key, self.clock()
        ):
            return {"sucesso": False, "erro": "limite_de_agenda"}
        # Calendar's durable receipts/Google event ID close timeout-after-commit gap.
        result = self.store.operation(
            key,
            row["id"],
            name,
            payload,
            lambda k: self.calendar.call(row["id"], k, name, args),
            now=self.clock(), authority=self.authority,
        )
        self.guard(row["id"], row["version"])
        return result

    def tick(self):
        if not self.prepare():
            return False
        row = (self.store.claim(self.clock(), authority=self.authority)
               if self.authority else self.store.claim(self.clock()))
        if not row:
            return False
        ident, version = row["id"], row["version"]
        try:
            if not self.policy.allows(row["phone"]):
                self.store.finish(ident, version, paused=True, reason="politica")
                return True
            draft = row.get("draft")
            if not draft or draft.get("version") != version or (self.snapshot and draft.get("configuration") != self.snapshot.fence()):
                output = self.model.run(
                    self.context(row), lambda n, a: self.action(row, n, a)
                )
                validate_output(output,
                                self.snapshot.payload["roles"]["atendimento"]["actions"] if self.snapshot else None,
                                self.snapshot.payload["limits"] if self.snapshot else None)
                self.guard(ident, version)
                draft = {
                    "version": version,
                    **({"configuration": self.authority.data()} if self.authority else {}),
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
                    self.check_configuration()
                    receipt = self.crm.command(ident, version, key, "send", text=block,
                                               **(self.authority.data() if self.authority else {}))
                    if receipt.get("status") != "sent":
                        raise PendingReceipt()
                    continue
                if not self.store.quota_key(ident, ["send"], self.setting("limits", "messages_per_hour", 20), key, self.clock()):
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
                behavior = self.snapshot.payload["behavior"] if self.snapshot else {}
                interval = behavior.get("recheck_ms", 500) / 1000
                cap = behavior.get("first_typing_cap_ms" if index == 0 else "next_typing_cap_ms", 6000 if index == 0 else 12000) / 1000
                remaining = min(cap, max(interval, len(block) * 60 / behavior.get("typing_cpm", 1500)))
                while remaining > 0:
                    self.sleep(min(interval, remaining))
                    remaining -= interval
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
        except ConfigurationUnavailable:
            # No retries/handoff while control is unavailable. Preserve draft and queue.
            self.store.finish(ident, version, lease=self.clock() + 2,
                              attempts=max(0, row["attempts"] - 1))
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
            if row["attempts"] >= self.setting("runtime", "max_attempts", 3):
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
        if not self.prepare():
            return False
        rows = (self.store.analyse_due(self.clock(), silence=self.snapshot.payload["behavior"]["analysis_silence_ms"] / 1000,
                                        max_attempts=self.setting("runtime", "max_attempts", 3), authority=self.authority)
                if self.snapshot else self.store.analyse_due(self.clock()))
        for row in rows:
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
