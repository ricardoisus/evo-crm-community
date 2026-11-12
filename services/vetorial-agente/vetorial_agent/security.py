import hashlib
import hmac
import time


def signed(body: bytes, secret: str, now=None, method="POST", path="/events"):
    stamp = str(int(time.time() if now is None else now))
    return {
        "Content-Type": "application/json",
        "X-Vetorial-Time": stamp,
        "X-Vetorial-Signature": hmac.new(
            secret.encode(),
            f"{stamp}.{method.upper()}.{path}.".encode() + body,
            hashlib.sha256,
        ).hexdigest(),
    }


def verify(body, headers, secret, now=None, method="POST", path="/events"):
    stamp = headers.get("X-Vetorial-Time", headers.get("x-vetorial-time", ""))
    sig = headers.get("X-Vetorial-Signature", headers.get("x-vetorial-signature", ""))
    if (
        len(secret) < 32
        or not stamp.isdigit()
        or abs((time.time() if now is None else now) - int(stamp)) > 60
    ):
        return False
    return hmac.compare_digest(
        sig, signed(body, secret, int(stamp), method, path)["X-Vetorial-Signature"]
    )
