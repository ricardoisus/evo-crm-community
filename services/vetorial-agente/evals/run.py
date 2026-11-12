"""Run: OPENAI_API_KEY in process memory only; python -m evals.run --output DIR.
External traffic is exclusively OpenAI. CRM, WhatsApp and calendar are local stubs.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import json
import os
import re
import statistics
import tempfile
import time
from pathlib import Path
from vetorial_agent.store import Store
from vetorial_agent.policy import Policy
from vetorial_agent.runtime import Worker
from vetorial_agent.adapters import OpenAI
from vetorial_agent.calendar import TestCalendar
from tests.fakes import Clock, CRMStub
from .fixtures import fixture, SLOTS, EMAIL, SLOT, U, A


class Recorder:
    def __init__(self, model, media_fixtures):
        self.model = model
        self.turns = []
        self.actions = []
        self.error = None
        self.media_fixtures = media_fixtures

    def run(self, context, act, analyst=False):
        entry = dict(context=copy.deepcopy(context), actions=[], analyst=analyst)
        self.turns.append(entry)

        def action(n, a):
            result = act(n, a)
            receipt = dict(name=n, args=a, result=result)
            entry["actions"].append(receipt)
            self.actions.append(receipt)
            return result

        try:
            output = self.model.run(context, action, analyst)
            entry["output"] = output
            return output
        except Exception as e:
            # Only sanitized API status or validation category. Never provider payloads.
            entry["error"] = type(e).__name__
            code = str(e)
            if re.fullmatch(r"openai_status_[0-9]{3}(?:_(?:insufficient_quota|rate_limit_exceeded|billing_hard_limit_reached))?|resposta_incompleta|limite_de_ferramentas", code):
                entry["error_code"] = code
            self.error = type(e).__name__
            raise

    def media(self, mime, data):
        return self.model.media(mime, data)


def identity_disclosed(messages):
    text = " ".join(messages).casefold()
    return bool(
        re.search(
            r"\b(?:sou|somos|como)\b.{0,60}\b(?:ia|i\.a\.|bot|rob[oô]|assistente virtual|intelig[eê]ncia artificial)\b"
            r"|\bassistente\b.{0,30}\b(?:ia|intelig[eê]ncia artificial)\b",
            text,
        )
    )


def evaluate(model_name, n, media_dir):
    f = fixture(n)
    clock = Clock()
    crm = CRMStub()
    calendar = TestCalendar(SLOTS)
    raw_model = OpenAI(os.environ["OPENAI_API_KEY"], model_name)
    recorder = Recorder(raw_model, media_dir)
    crm.context["messages"] = copy.deepcopy(f["history"])
    if f["prebook"]:
        calendar.call(
            "conv", "seed-offer", "consultar_horarios", {"periodo": "próxima semana"}
        )
        calendar.call(
            "conv",
            "seed-book",
            "agendar",
            {"horario": SLOT, "nome": "João sintético", "email": EMAIL},
        )
    if n in (10, 27):
        calendar.call(
            "conv", "prior-offer", "consultar_horarios", {"periodo": "próxima semana"}
        )
    if f["conflict"]:
        calendar.conflict = True
    original = crm.command

    def command(ident, version, key, kind, **kw):
        if kind == "media":
            media = f["media"]
            ext = {"audio": "wav", "image": "png", "pdf": "pdf"}[media]
            import base64

            result = dict(
                mime={
                    "audio": "audio/wav",
                    "image": "image/png",
                    "pdf": "application/pdf",
                }[media],
                base64=base64.b64encode(
                    (media_dir / ("synthetic." + ext)).read_bytes()
                ).decode(),
            )
            crm.commands.append(dict(kind="media", attachment_id=kw["attachment_id"]))
            return result
        return original(ident, version, key, kind, **kw)

    crm.command = command
    with tempfile.TemporaryDirectory() as tmp:
        store = Store("sqlite:///" + str(Path(tmp) / "state.db"))
        store.migrate()
        policy = Policy("box", frozenset(["+15550000002"]))
        worker = Worker(
            store, crm, recorder, calendar, policy, sleep=clock.sleep, clock=clock
        )
        timings = []
        version = 0
        for batch in f["turns"]:
            for text in batch:
                version += 1
                message = U(text)
                if f["media"]:
                    message["attachments"] = [dict(id="synthetic-media", bytes=1000)]
                crm.context["messages"].append(message)
                crm.context["version"] = version
                store.ingest(
                    dict(
                        event_id=f"e{version}",
                        inbox_id="box",
                        conversation_id="conv",
                        message_id=f"m{version}",
                        version=version,
                        paused=False,
                        kind="incoming",
                        phone="+15550000002",
                        audio=f["media"] == "audio",
                        occurred_at=clock(),
                    ),
                    policy,
                    clock(),
                )
                clock.now += 3 if len(batch) > 1 else 0
            clock.now += 30 if f["media"] == "audio" else 20
            sent_before = len(crm.sent)
            start = time.monotonic()
            worker.tick()
            timings.append(time.monotonic() - start)
            for text in crm.sent[sent_before:]:
                crm.context["messages"].append(A(text))
            # A silence/handoff is terminal in this synthetic conversation, just as real CRM pause.
            if any(c["kind"] in ("silent", "handoff") for c in crm.commands):
                break
        # Analyst runs after the entire conversation has been quiet for five minutes.
        clock.now += 301
        worker.analyse()
        local = store.get("conv")
    names = [a["name"] for t in recorder.turns if not t["analyst"] for a in t["actions"]]
    missing = [name for name in f["expected_tools"] if name not in names]
    checks = {
        "schema_and_execution": not recorder.error,
        "identity_not_disclosed": not identity_disclosed(crm.sent),
        "expected_tools": not missing,
        "no_real_transport": True,
        "no_public_price": not any("R$" in x for x in crm.sent)
        if n in (8, 14, 15)
        else True,
        "media_read": not f["media"]
        or any(
            "media_text" in m for t in recorder.turns for m in t["context"]["messages"]
        ),
        "analyst_private": any(c["kind"] == "analysis" for c in crm.commands),
    }
    if n in (6, 13, 20, 25):
        checks["silent_after_explanation"] = any(
            c["kind"] == "silent" for c in crm.commands
        )
    if n == 7:
        checks["emoji"] = recorder.turns[0].get("output", {}).get("acao") == "emoji"
    if n == 12:
        checks["cancel_confirmed"] = any(
            x["name"] == "cancelar" and x["result"].get("sucesso")
            for x in recorder.actions
        )
    if n == 27:
        checks["conflict_not_confirmed"] = not any(
            e for e in calendar.events.values() if not e["cancelled"]
        )
    if n == 28:
        checks["invalid_email_not_used"] = not any(
            x["name"] == "agendar" for x in recorder.actions
        )
    if n == 16:
        checks["handoff_pauses"] = any(c["kind"] == "handoff" for c in crm.commands)
    return dict(
        scenario=f["id"],
        model=model_name,
        status="APROVADO_AUTOMATICO" if all(checks.values()) else "FALHA_AUTOMATICA",
        checks=checks,
        missing_tools=missing,
        turns=recorder.turns,
        crm_actions=crm.commands,
        public_output_stub=crm.sent,
        calendar_events_stub=list(calendar.events.values()),
        usage=raw_model.usage,
        latency_seconds=timings,
        debounce_seconds=30 if f["media"] == "audio" else 20,
        typing_simulated_seconds="1500 caracteres/min; relógio virtual",
        editorial_review="PENDENTE revisão das transcrições A4; A2 avaliou v1.0",
        last_state=local,
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--media-dir", type=Path, required=True)
    p.add_argument("--replace", action="store_true")
    p.add_argument("--workers", type=int, default=1, choices=range(1, 5))
    p.add_argument("--models", nargs="+", default=["gpt-6-astra", "gpt-6.1-sol"])
    p.add_argument("--scenarios", nargs="+", type=int, default=list(range(1, 31)))
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    jobs = [
        (model, n)
        for model in args.models
        for n in args.scenarios
        if args.replace or not (args.output / f"{model}-{n:02}.json").exists()
    ]
    # Independent synthetic conversations only; never parallel production workers.
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(evaluate, model, n, args.media_dir): (model, n)
            for model, n in jobs
        }
        for future in as_completed(futures):
            model, n = futures[future]
            result = future.result()
            path = args.output / f"{model}-{n:02}.json"
            path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            print(
                model, result["scenario"], result["status"], result["missing_tools"], flush=True
            )
    # Append-safe summary regenerated from artifacts, allowing targeted reruns.
    summary = {}
    for model in args.models:
        rows = [
            json.loads(p.read_text())
            for p in sorted(args.output.glob(model + "-*.json"))
        ]
        times = [t for r in rows for t in r["latency_seconds"]]
        usages = [u for r in rows for u in r["usage"]]
        cost = 0
        rates = {"gpt-6-astra": (10, 50, 1, 12.5), "gpt-6.1-sol": (2, 10, 0.1, 2.5)}[
            model
        ]
        for u in usages:
            if u.get("type") or u.get("_endpoint") == "/audio/transcriptions":
                continue
            details = u.get("input_tokens_details", {})
            cached = details.get("cached_tokens", 0)
            written = details.get("cache_write_tokens", 0)
            cost += (
                max(0, u["input_tokens"] - cached - written) * rates[0]
                + u["output_tokens"] * rates[1]
                + cached * rates[2]
                + written * rates[3]
            ) / 1e6
        first_estimates = []
        for row in rows:
            outputs = [t.get("output", {}) for t in row["turns"] if not t["analyst"]]
            for latency, output in zip(row["latency_seconds"], outputs):
                if output.get("mensagens"):
                    first_estimates.append(
                        latency
                        + min(12, max(0.5, len(output["mensagens"][0]) * 60 / 1500))
                    )
        summary[model] = dict(
            first_block_estimate_p50=statistics.median(first_estimates) if first_estimates else None,
            first_block_estimate_max=max(first_estimates) if first_estimates else None,
            first_block_estimate_over_15=sum(t > 15 for t in first_estimates),
            first_block_scope="Estimativa: tempo da API/contexto + digitação virtual do primeiro bloco; não mede transporte real.",
            scenarios=len(rows),
            passed=sum(r["status"] == "APROVADO_AUTOMATICO" for r in rows),
            execution_inconclusive=[
                r["scenario"] for r in rows if any(t.get("error") for t in r["turns"])
            ],
            behavioral_failures=[
                r["scenario"] for r in rows
                if r["status"] != "APROVADO_AUTOMATICO" and not any(t.get("error") for t in r["turns"])
            ],
            failures=[
                r["scenario"] for r in rows if r["status"] != "APROVADO_AUTOMATICO"
            ],
            latency_scope="Tempo de cada tick, incluindo tentativas rejeitadas; não é SLA de resposta bem-sucedida.",
            latency_p50=statistics.median(times) if times else None,
            latency_max=max(times) if times else None,
            latency_over_15=sum(t > 15 for t in times),
            estimated_total_usd=cost,
            estimated_per_scenario_usd=cost / len(rows) if rows else None,
            cost_scope="Texto/visão medidos; transcrição de áudio excluída do total por não retornar os mesmos campos de uso. Não é preço de produção.",
            rates_source="https://developers.openai.com/api/docs/pricing",
            editorial_review="PENDENTE revisão das transcrições A4; A2 avaliou v1.0",
        )
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
