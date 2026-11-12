import copy


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, n):
        self.now += n


class CRMStub:
    def __init__(self):
        self.context = {
            "version": 1,
            "paused": False,
            "allowed": True,
            "eligible": True,
            "nome_contato": "João Sintético",
            "campos_negocio": {},
            "messages": [],
        }
        self.commands = []
        self.receipts = {}
        self.sent = []
        self.hook = None

    def request(self, ident):
        return copy.deepcopy(self.context)

    def history(self, ident):
        return self.request(ident)

    def command(self, ident, version, key, kind, **payload):
        if key in self.receipts:
            return self.receipts[key]
        assert version == self.context["version"]
        assert (kind in ("analysis", "analysis_media") and self.context.get("analysis_allowed", True)) or (
            not self.context["paused"] and self.context["allowed"]
        )
        self.commands.append(dict(key=key, kind=kind, **payload))
        if kind == "send":
            self.sent.append(payload["text"])
            result = {"sucesso": True, "status": "sent", "message_id": key}
        elif kind in ("media", "analysis_media"):
            result = {"mime": "audio/ogg", "base64": "c3ludGhldGlj"}
        else:
            result = {"sucesso": True, "status": "done"}
        self.receipts[key] = result
        if self.hook:
            self.hook(kind)
        return result


class ModelStub:
    def __init__(self, output=None):
        self.output = output or {
            "acao": "responder",
            "mensagens": ["Olá", "Como está a captação?"],
            "motivo_interno": "teste",
        }
        self.calls = 0
        self.hook = None
        self.contexts = []

    def run(self, context, act, analyst=False):
        self.contexts.append(context)
        self.calls += 1
        if self.hook:
            self.hook()
        if analyst:
            return {
                "resumo": "Médico em São Paulo",
                "campos_atualizados": {"cidade": "São Paulo"},
                "etiquetas": [],
                "etapa_sugerida": "Avaliação humana",
                "justificativa": "Precisa de avaliação",
                "nota_privada": "Interno",
            }
        return self.output

    def media(self, mime, data):
        return "Sou cardiologista em São Paulo"
