from dataclasses import dataclass
import re
from dataclasses import field


@dataclass(frozen=True)
class Policy:
    inbox_id: str
    allowlist: frozenset = frozenset()
    internal: frozenset = frozenset()
    analysis_all: bool = False
    all_contacts: bool = False

    attendance_enabled: bool = True
    analysis_enabled: bool = True
    behavior: dict = field(default_factory=dict)

    def allows(self, phone):
        return bool(
            self.attendance_enabled and self.inbox_id
            and re.fullmatch(r"\+[1-9]\d{6,14}", phone or "")
            and (self.all_contacts or phone in self.allowlist)
            and phone not in self.internal
        )

    def allows_analysis(self, phone):
        return bool(
            self.analysis_enabled and self.inbox_id
            and re.fullmatch(r"\+[1-9]\d{6,14}", phone or "")
            and phone not in self.internal
            and (self.analysis_all or self.all_contacts or phone in self.allowlist)
        )
