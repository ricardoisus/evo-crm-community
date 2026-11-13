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

## I32: fundação de telemetria, parcial (12/11/2026)

- `telemetry.py`: ledger e outbox aditivos registrados por `Store.migrate()`; nunca DDL no startup.
- `Telemetry.record_batch()` grava execução/uso e outbox na mesma transação. IDs UUID; índice único de evento e de assunto por deployment/kind (execution_id/call_id). Colisão recusa substituição; reentrega usa os mesmos bytes lógicos.
- `flush()` persiste tentativa/backoff antes do transporte e confirma somente IDs reconhecidos. Queda/ACK perdido preserva o lote; reinício recupera. Não há garantia exactly-once HTTP, nem exclusão dos recibos.
- `HTTPDelivery`: POST HMAC para `/internal/vetorial_agent/telemetry`, envelope `{events:[...]}`, resposta proposta `{acknowledged:[UUID]}`; timeout/HTTP inválido/erro não expõe body nem headers. **Endpoint CRM ainda inexistente; não conectar em produção.**
- Schema fechado aceita somente IDs/tokens/inteiros/estados fixos. Custo em micro-USD fornecido pelo produtor, `unknown` exige null; não calcula tarifa. Integrações: openai/groq/calendly/google_gateway, somente configured/absent/error/unverified. Não aceita prompt, telefone, texto livre ou credencial.
- `health()` é função testada para 45/90s e divergência de revisão/hash. Não é ainda status derivado no CRM; heartbeat não é emitido pelo loop.
- Testes SQLite/HTTP dublê: reinício, backoff, ACK perdido, duplicidade, colisão/rollback de batch, secret em resposta HTTP, saúde e limites de valores. Novo teste PostgreSQL concorrente está condicionado a POSTGRES_TEST_URL e ainda não executado.
- **PENDENTE R5/R6:** ligar captura por chamada e execução ao adaptador/worker (incluindo falha/unknown), heartbeat15s/ack real, tarifação versionada/áudio, recibos de agenda e envio; implementar ingestão HMAC e dedup CRM, métricas live/test/mês SP/p50/p95, autorização/paginação e probes reais sem gasto. Antes de adotar, fechar schema com produtor/CRM; os eventos terminais imutáveis atuais ainda não modelam conciliação unknown→known. Não liberar I4 com este incremento.
- C8 já possui índice/resposta24h no CRM desde I3; não foi revalidado em PostgreSQL na I32.
- Sem merge/deploy, API paga ou efeito externo. Piloto A14 preservado.

## I33: sandbox isolado, incremento parcial (12/11/2026)

- `sandbox.py`: tabelas aditivas registradas por `Store.migrate`, sem DDL no startup HTTP.
- Sessão emitida no servidor, revisão fixa, propósito/critério/amostra3–5; expira em30min e na virada do dia São Paulo. Padrão50.000 micro-USD, máximo500.000; envelope fixo Vetorial1.000.000/dia e operador500.000/dia, compartilhado entre deployments. Nenhuma tarifa real calculada.
- O teto inteiro da sessão é alocado uma vez no envelope. Fechar/expirar não devolve essa alocação no dia. É deliberadamente conservador, ainda não implementa a conciliação fina do contrato CRM.
- Reserva de pior custo do turno inteiro antes de montar o modelo; callback confiável deve cobrir todas as rodadas/contexto/mídia. Sem estimativa confiável recusa. Consumo desconhecido, erro ou crash conserva reserva e bloqueia novos turnos, sem retry. Idempotência por UUID e hash do texto; resultado conhecido é reapresentado sem inferência.
- UPDATE transacional serializa criação de envelopes e turnos. SQLite concorrente testado; teste PostgreSQL presente, mas não executado sem banco sintético.
- `SandboxAdapters` recebe somente FakeCRM/FakeAgenda/NoopTransport. Não recebe CRM/Store live, Worker, downloader ou agenda real. Usa `OpenAI.run` com snapshot e validações existentes em teste com provedor dublê. Sem quotas de20/h, operações ou telemetria live.
- POST privado `/test_sessions/:session_id/turns`: HMAC do path/body, campos exatos `request_id`/`text`, limite20KB; callback confiável resolve autorização CRM. Sem sandbox/autorizações injetados responde503. Sem HMAC401; negado403; entrada inválida422; limite/consumo incerto429. Não há porta nova.
- `production()` não injeta o sandbox nem contém criação de sessão. Não há caminho pago habilitado. Factory/estimativa/usage são pontos de injeção, não uma integração final.

PENDENTE I33/R7: implementar sessões e autorização no CRM (owner/admin/inbox/revisão e grupo de etapa), orçamento canônico Rails ou protocolo atômico único com este ledger, estimador conservador/tarifa versionada e usage por chamada. Não pode haver dois ledgers independentes autorizando o mesmo gasto. Ligar bridge HMAC e histórico persistente do chat; hoje cada turno recebe apenas o texto atual e não há mídia/analista no sandbox. Provar limites por chamada dentro de loop e conciliação de uso unknown. Não habilitar pagamento antes disso.

PENDENTE I33/R2: ativação/desafio nonce60s uso único, binding HTTP, expediente e grant de remoção de responsável permanecem ausentes. C2 já tem implementação provisória CRM desde I3, sem novo aceite. Schemas aninhados/constraints e matriz RSpec requests/autorização/concorrência ainda incompletos. R3/R5/R6 e ensaio de continuidade anteriores permanecem pendentes. Sem merge/deploy.
