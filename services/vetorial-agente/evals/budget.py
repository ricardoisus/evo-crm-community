"""Persistent, sequential spend gate. No request contents or credentials in ledger."""
import json
from pathlib import Path

RATES = {"gpt-6-luna": (0.10, 0.50, 0.01, 0.125, 0.25),
         "gpt-6.1-sol": (2.0, 10.0, 0.10, 2.50, 5.0)}


class BudgetStop(BaseException):
    """Must escape worker retry/error handlers immediately."""


class Meter:
    def __init__(self, path, limit=0.50, groq_limit=0.10):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {
            "openai_usd": 0, "groq_usd": 0, "calls": [], "pending": None,
            "rates_source": "https://developers.openai.com/api/docs/pricing",
            "groq_source": "https://console.groq.com/docs/speech-to-text",
        }
        self.limit, self.groq_limit = limit, groq_limit
        if self.data.get("pending") or self.data.get("stopped"):
            raise BudgetStop("BLOQUEADO: medidor pendente ou interrompido; não repetir")
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n")
        tmp.replace(self.path)

    def stop(self, reason):
        self.data["stopped"] = reason
        self.save()
        raise BudgetStop(reason)

    def reserve(self, provider, maximum):
        limit = self.limit if provider == "openai" else self.groq_limit
        if self.data[provider + "_usd"] + maximum > limit:
            self.stop("BLOQUEADO: teto de gasto")
        self.data["pending"] = {"provider": provider, "maximum_usd": maximum}
        self.save()

    def settle(self, provider, cost, **meta):
        self.data[provider + "_usd"] += cost
        self.data["calls"].append(dict(provider=provider, cost_usd=cost, **meta))
        self.data["pending"] = None
        self.save()
        limit = self.limit if provider == "openai" else self.groq_limit
        if self.data[provider + "_usd"] >= limit:
            self.stop("BLOQUEADO: teto de gasto")

    def openai(self, client, path, **kwargs):
        payload = kwargs["json"]
        model = payload["model"]
        rates = RATES[model]  # Unapproved models never reach the network.
        # UTF-8 bytes upper-bound text tokens, including tools/schema overhead.
        # Inline image is capped by payload bytes too (conservative reservation).
        size = len(json.dumps(payload, ensure_ascii=False).encode()) + 2048
        write_rate = rates[4] if payload.get("prompt_cache_options", {}).get("ttl") == "12h" else rates[3]
        maximum = (size * max(rates[0], write_rate) + payload["max_output_tokens"] * rates[1]) / 1e6
        self.reserve("openai", maximum)
        try:
            result = client(path, **kwargs)
        except Exception as exc:
            code = str(exc)
            if any(x in code for x in ("insufficient_quota", "credit_balance", "billing_hard_limit")):
                self.data["pending"] = None
                self.stop("BLOQUEADO: crédito OpenAI")
            # Without usage a timeout can still be billed. Keep reservation and halt.
            self.stop("BLOQUEADO: execução OpenAI sem uso confirmado")
        u = result.get("usage")
        if not u or "input_tokens" not in u or "output_tokens" not in u:
            self.stop("BLOQUEADO: uso OpenAI ausente")
        details = u.get("input_tokens_details", {})
        cached = details.get("cached_tokens", 0)
        written = details.get("cache_write_tokens", 0)
        cost = (max(0, u["input_tokens"]-cached-written)*rates[0] + u["output_tokens"]*rates[1]
                + cached*rates[2] + written*write_rate) / 1e6
        self.settle("openai", cost, model=model, usage=u)
        return result
