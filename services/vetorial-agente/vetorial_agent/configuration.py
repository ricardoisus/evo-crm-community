"""Versioned CRM snapshot protocol. This module never reads behavior from env/files.

Only an explicit revocation invalidates an unexpired lease. Transient fetch or
validation failure keeps the previously verified snapshot until its own deadline.
"""
import copy
import hashlib
import json
import time
from pathlib import Path
from jsonschema import Draft7Validator

SNAPSHOT_SCHEMA = json.loads(Path(__file__).with_name("snapshot.schema.json").read_text())
SNAPSHOT_VALIDATOR = Draft7Validator(SNAPSHOT_SCHEMA)
from dataclasses import dataclass

MODELS = frozenset(("gpt-6-luna", "gpt-6.1-sol"))
ROOT_KEYS = frozenset("schema_version deployment_id revision policy_epoch binding activation roles behavior limits integrations provider runtime".split())


class InvalidConfiguration(ValueError):
    pass


class ConfigurationUnavailable(RuntimeError):
    pass


def canonical(value):
    def check(v):
        if isinstance(v, dict):
            if any(not isinstance(k, str) for k in v):
                raise InvalidConfiguration("Chave inválida")
            for child in v.values():
                check(child)
        elif isinstance(v, list):
            for child in v:
                check(child)
        elif type(v) not in (str, int, bool, type(None)):
            raise InvalidConfiguration("A configuração aceita apenas números inteiros")
    check(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(payload):
    return hashlib.sha256(canonical(payload)).hexdigest()


def validate(payload):
    canonical(payload)
    if not SNAPSHOT_VALIDATOR.is_valid(payload):
        raise InvalidConfiguration("Configuração inválida")
    activation = payload["activation"]
    if "pilot" in (activation["atendimento"], activation["analista"]) and not activation["allowlist"]:
        raise InvalidConfiguration("Lista piloto vazia")
    hours = payload["behavior"]["office_hours"]
    if hours["enabled"] != bool(hours["windows"]):
        raise InvalidConfiguration("Expediente inválido")
    windows = sorted(hours["windows"], key=lambda w: (w["weekday"], w["start"]))
    for w in windows:
        if w["start"] >= w["end"]:
            raise InvalidConfiguration("Expediente inválido")
    for a, b in zip(windows, windows[1:]):
        if a["weekday"] == b["weekday"] and a["end"] > b["start"]:
            raise InvalidConfiguration("Expediente sobreposto")
    if set(payload) != ROOT_KEYS or payload["schema_version"] != 1:
        raise InvalidConfiguration("Versão de configuração inválida")
    for name in ("revision", "policy_epoch"):
        if type(payload[name]) is not int or payload[name] < 1:
            raise InvalidConfiguration("Revisão inválida")
    if set(payload["roles"]) != {"atendimento", "analista"}:
        raise InvalidConfiguration("Papéis inválidos")
    for role in payload["roles"].values():
        if set(role) != {"model", "prompt", "prompt_version", "actions", "output_schema"}:
            raise InvalidConfiguration("Papel inválido")
        if role["model"] not in MODELS or not 0 < len(role["prompt"].encode("utf-8")) <= 131072:
            raise InvalidConfiguration("Modelo ou prompt inválido")
    return payload


@dataclass(frozen=True)
class Snapshot:
    # Do not hand mutable cached data to adapters; pin() always returns a deep copy.
    payload: dict
    sha256: str
    expires_at: float

    def fence(self):
        return {"revision": self.payload["revision"], "policy_epoch": self.payload["policy_epoch"], "sha256": self.sha256}


class ConfigurationCache:
    def __init__(self, fetch, deployment_id, clock=time.monotonic):
        self.fetch, self.deployment_id, self.clock = fetch, deployment_id, clock
        self._snapshot = None
        self._next_poll = 0

    def refresh(self, force=False):
        now = self.clock()
        if not force and now < self._next_poll:
            return
        self._next_poll = now + 10
        try:
            envelope = self.fetch(self.deployment_id)
        except Exception:
            return  # network outage cannot renew a lease
        if envelope.get("revoked") is True:
            self._snapshot = None
            return
        try:
            payload = copy.deepcopy(envelope["payload"])
            validate(payload)
            if payload["deployment_id"] != self.deployment_id or digest(payload) != envelope["sha256"]:
                raise InvalidConfiguration("Hash divergente")
            if type(envelope["lease_ms"]) is not int or envelope["lease_ms"] != 120000:
                raise InvalidConfiguration("Prazo inválido")
            old = self._snapshot
            if old and (payload["revision"] < old.payload["revision"] or payload["policy_epoch"] < old.payload["policy_epoch"]):
                raise InvalidConfiguration("Revisão regressiva")
            self._snapshot = Snapshot(payload, envelope["sha256"], now + 120)
        except (KeyError, TypeError, ValueError):
            return

    def pin(self):
        self.refresh()
        if self._snapshot is None or self.clock() >= self._snapshot.expires_at:
            raise ConfigurationUnavailable("Configuração indisponível")
        return copy.deepcopy(self._snapshot)


class EffectFence:
    """Execution-scoped authority. Never renew or replace the pinned revision."""
    def __init__(self, snapshot, cache):
        self.snapshot = copy.deepcopy(snapshot)
        self.cache = cache

    def check(self):
        if self.cache.pin().fence() != self.snapshot.fence():
            raise ConfigurationUnavailable("Revisão ou epoch divergente")
        return self.snapshot.fence()

    def data(self):
        return self.check()
