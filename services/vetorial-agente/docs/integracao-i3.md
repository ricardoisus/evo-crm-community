# I3: contrato de configuração isolado

Data: 12/11/2026. Etapa: I3. Autor: Theo, CTO · Codex Sol.
Status: **INCOMPLETO**, sem deploy. Fontes: plano §8, spec I1 §§3/9/10 e revisão I2 C3/C4.

- configuration.py implementa canonicalização, digest, pin por cópia e cache de 120s/poll10s.
- Falha de rede/hash não renova a lease; expiração/startup sem snapshot fecham; revogação explícita remove cache.
- Snapshot de teste é byte a byte igual ao fixture Rails; inclui acento, emoji e CRLF.
- import_a14.candidate recebe apenas parâmetros explícitos não secretos e preserva bytes dos prompts A14, contrato, adendo e valores conhecidos.
- Nenhuma função nova é chamada pelo build/worker de produção. Variáveis/arquivos A14 continuam sendo usados no app atual.
- Portanto, não atende ainda ao R3. O teste de equivalência é parcial, não comprova execução idêntica nem migração/rollback.

Próximo Theo: integrar ao worker e todos os adaptadores sem fallback, snapshot fixado por execução,
política/debounce/agenda/digitação/limites configuráveis, migração aditiva do store, ledger/outbox/telemetria,
readiness e heartbeat; sandbox com adaptadores separados e orçamento reservado antes da chamada.
Ensaiar concorrência/restart/rollout/rollback com CRM sintético antes de liberar I4.
Testes atuais são offline e não autorizam inferência paga.
