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

## I31, 12/11/2026: continuação parcial de R3

- `OpenAI(..., snapshot=cache.pin())` copia a revisão e usa seus modelos, projeto, prompts, schemas, ações, limites de resposta/ferramentas, tier, reasoning, fuso e adendo. Sem snapshot, mantém A14.
- `Worker(..., configuration_cache=cache, configure=factory)` fixa snapshot antes de claim/analyse_due. `factory(snapshot)` retorna `(model, calendar, policy)` inteiramente derivados da revisão. Config indisponível não consome fila. `guard` confere revisão/epoch/hash e lease novamente; indisponibilidade após claim preserva rascunho e desconta a tentativa.
- Esses argumentos são pontos de integração testados, **ainda não conectados a `app.build()`**. Não declarar R3 pronto nem promover `control_source` com este código.
- Falta: factory/HTTP e seleção exclusiva dos modos no boot; parâmetros do store/agenda/mídia e fences completos; importação observada A14 e ensaio integral. O contrato global ainda é carregado no import do módulo para compatibilidade A14; remover essa dependência no boot CRM antes do aceite.
- Provas sem rede paga: `tests/test_snapshot_model.py` compara chamadas do atendimento (inclusive ferramenta e saída), request do analista e influência de edição do snapshot. O analista usa interrupção após capturar request; não comprova classificação final. `tests/test_snapshot_worker.py` prova suspensão antes de claim, troca de revisão e caminho legado separado por injeção. Não é ensaio HTTP nem prova de produção.
