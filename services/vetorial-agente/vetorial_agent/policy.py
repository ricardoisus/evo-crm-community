from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Policy:
    inbox_id: str
    allowlist: frozenset = frozenset()
    internal: frozenset = frozenset()

    def allows(self, phone):
        return bool(
            self.inbox_id
            and re.fullmatch(r"\+[1-9]\d{6,14}", phone or "")
            and phone in self.allowlist
            and phone not in self.internal
        )
