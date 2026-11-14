"""Read-only probes. Never parse/return provider bodies, headers or exception text."""
import time
from datetime import datetime, timedelta, timezone
import httpx
from .security import signed

NAMES = ("openai", "groq", "calendly", "google_gateway", "evolution")
STATES = ("configured", "absent", "error")


class IntegrationProbes:
    def __init__(self, routes, *, client=None, clock=time.monotonic):
        self.routes = routes
        self.client = client or httpx.Client(timeout=10, follow_redirects=False)
        self.clock = clock
        self.cached, self.expires = None, 0

    def __call__(self):
        if self.cached is not None and self.clock() < self.expires:
            return dict(self.cached)
        result = {}
        for name in NAMES:
            route = self.routes.get(name)
            if not route:
                result[name] = "absent"
                continue
            try:
                url, headers, *body = route
                headers = headers() if callable(headers) else headers
                # n8n uses POST even for read-only list. All other probes use GET.
                kwargs = {"json": body[0]()} if name == "google_gateway" and body else {}
                method = "POST" if kwargs else "GET"
                with self.client.stream(method, url, headers=headers, **kwargs) as response:
                    result[name] = ("configured" if response.status_code == 200 else
                                    "absent" if name == "evolution" and response.status_code == 204 else "error")
            except Exception:
                result[name] = "error"
        self.cached, self.expires = result, self.clock() + 60
        return dict(result)


def runtime_probes(api_key, groq_key, calendly_token, gateway, gateway_secret,
                   crm_url, bridge_secret, deployment_id, gateway_context=lambda: {}):
    path = f"/internal/vetorial_agent/integrations/{deployment_id}"
    def calendar_query():
        now = datetime.now(timezone.utc)
        return {**gateway_context(), "op": "list", "timeMin": now.isoformat(),
                "timeMax": (now + timedelta(minutes=1)).isoformat()}
    return IntegrationProbes({
        "openai": ("https://api.openai.com/v1/models", {"Authorization": f"Bearer {api_key}"}) if api_key else None,
        "groq": ("https://api.groq.com/openai/v1/models", {"Authorization": f"Bearer {groq_key}"}) if groq_key else None,
        "calendly": ("https://api.calendly.com/users/me", {"Authorization": f"Bearer {calendly_token}"}) if calendly_token else None,
        "google_gateway": (gateway, {"X-Vetorial-Calendar-Key": gateway_secret}, calendar_query) if gateway and gateway_secret else None,
        # Evolution belongs to the CRM. Its private probe returns 200 only if configured.
        "evolution": (crm_url.rstrip("/") + path, lambda: signed(b"", bridge_secret, method="GET", path=path)),
    })
