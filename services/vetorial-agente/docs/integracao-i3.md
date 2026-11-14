> Estado vigente J12: backend aceito localmente para I4; PR rascunho, sem deploy. As pendências históricas abaixo são substituídas pelo aceite J12 ao final.

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

## J1 — Regressão completa com PostgreSQL

Data: 13/11/2026. Autor: Theo, CTO · Codex Sol. Status: testado localmente, sem deploy.
Fonte: entrega `I3-backend-runtime.md` §J1 da frente Evo CRM Agentes.

```sh
cd /Users/ricardohenrique/Projetos/evo-crm/_evo-i3-runtime/services/vetorial-agente
POSTGRES_TEST_URL=postgresql+psycopg://postgres@127.0.0.1:55439/vetorial_agent_a12_test PYTHONPATH=. /Users/ricardohenrique/Projetos/evo-crm/_evo-agenda-runtime/services/vetorial-agente/.venv/bin/python -m pytest tests -q
```

Resultado: **142 passaram, zero ignorados**, incluindo os quatro testes de `tests/test_postgres.py`; um aviso preexistente Starlette/httpx. Modelo por dublê, US$0. Contêineres sintéticos `evo-i3-postgres` e `evo-i3-redis` iniciados; Docker permanece aberto. Este resultado resolve a lacuna de ambiente da I34 e não constitui aceite integrado R1–R8.

## J2 — seleção exclusiva de fonte no boot

13/11/2026 · Theo/CTO · implementado/testado, sem implantação.
`VETORIAL_AGENT_CONTROL_SOURCE=crm` exige identificação técnica
`VETORIAL_AGENT_DEPLOYMENT_ID`; padrão `legacy_transition` conserva A14.
CRM GET `/internal/vetorial_agent/config/:deployment_id` usa HMAC método/path.
Boot, ingresso HTTP e factory usam cache/lease; modelo, prompts, contratos,
ativação, caixa e tempos vêm do snapshot. Contrato legado é carregado sob demanda.
Sem snapshot: worker não faz claim; `/ready` e `/events` retornam 503. A outbox
CRM mantém o evento para reentrega. Não há ACK ou fallback para env/arquivo.
`build(gateway_routes={ref: URL})` recebe mapa técnico confiável para resolver
`google_gateway_ref`; padrão vazio fecha agenda. A ligação da rota real pertence
à integração/deploy posterior; não inferir endpoint de produção nesta etapa.
Teste completo J1 com POSTGRES_TEST_URL: 145 passaram, zero ignorados;
um aviso preexistente Starlette/httpx. Novo teste bloqueia leitura de arquivos,
altera env, publica revisão sintética e confere HTTP/debounce/fila preservada.
Não equivale ao aceite integral R3: guards/efeitos, mídia/agenda e ensaio de
migração continuam nos itens J seguintes. Sem API paga, merge ou produção.

## J3 — policy, store, agenda e mídia sob snapshot

Data: 13/11/2026. Etapa: J3. Autor: Theo, CTO · Codex Sol principal.
Status: implementado e testado; sem merge, deploy ou evento real.
Fontes: plano §8; I1 §3.3; I2 C3–C5; passagem J2; tests/test_snapshot_components.py.

- EffectFence é fixado por execução, separado do cache; recusa revisão/hash/epoch divergentes e lease vencida.
- Política e ingresso usam o mesmo fence, inclusive lista, modos e debounce. Ingresso divergente responde 503 sem ACK.
- Store verifica fence em ingest/claim/analyse_due/operation. Eventos/operações guardam o fence em JSON existente, sem migração.
- Worker deriva quotas de mensagens/agenda, tentativas, histórico e mídia do snapshot; rascunhos registram fence.
- CRM recebe revision/policy_epoch/sha256 em comandos, inclusive leitura de mídia e consulta de recibo de envio.
- Recibo concluído idêntico pode ser reaproveitado sob revisão atual sem executar de novo; preserva o fence original. Operação pendente com payload/fence diferente recusa colisão.
- Agenda usa event_type, calendar_id, duração, fuso, horizonte e validade da oferta do snapshot. Ofertas/recibos guardam fence; oferta de outra revisão exige nova consulta.
- Calendly revalida antes de cada página; gateway Google revalida antes de cada chamada e leva calendar_id/fence no envelope. Cancelamento reautoriza antes de delete.
- Modelo/mídia e Groq revalidam antes do provedor; bytes/páginas/anexos vêm do snapshot. Usage em memória conserva o fence da chamada aceita, mesmo se a revisão mudar depois.
- Protocolo Groq continua whisper-large-v3-turbo/pt; não há configuração nova ou fallback de provedor.
- Testes: comando J1 com POSTGRES_TEST_URL, 159 passaram, zero ignorados; aviso Starlette/httpx preexistente. Dublês, US$0; diff-check aprovado.
- Limite: teste de componentes sintético. Gateway vivo, revalidação remota de efeitos e ensaio integrado/rollout permanecem nas etapas próprias; não é aceite integral R3.
- Rota confiável vetorial-calendar-v1 ainda exige resolução e compatibilidade de calendar_id/fence no gateway antes de integração/deploy (pendência J2). Nenhuma alteração no gateway ou produção nesta etapa.

## J5 — revisão A14 em memória e equivalência

- Fonte: manifests versionados `deploy/crm-production.json`, `deploy/stack.production.json` e prompts A14 do serviço. Nenhuma leitura de produção ou dos caminhos de secrets.
- `import_a14.import_review(expected_binding=..., expected_sha256=...)` monta revisão 1 somente em memória, recusa divergência entre manifests, identidade/caixa/pipeline e hash esperado. Não publica nem ativa.
- Hash da revisão A14: `bff855d82653f44cbe747fe2e8a4a19520d887563d74e2a40ea5c801224546a9`.
- `tests/test_a14_equivalence.py`: seis conversas sintéticas (texto, áudio, agenda, pausa, fora da lista e transferência), worker legado versus snapshot CRM, provider e mídia dublês. Compara decisões, requests completos com prompts, ferramentas e comandos de negócio.
- Metadados de protocolo CRM revision/policy_epoch/sha256 e sufixo hash da chave de cache de mídia são conferidos separadamente do conteúdo de negócio; não existem no protocolo legado. Não se declara igualdade byte a byte desses envelopes.
- Não comprova publicação Rails, transporte HTTP integrado ou evento real; pertencem ao ensaio posterior. Piloto preservado.

## J7 — connected telemetry

In CRM control mode, `build()` injects the durable telemetry producer into the worker.
Attendance/analysis attempts record succeeded/failed/discarded/uncertain executions;
each real OpenAI request (including tool rounds/vision) and Groq transcription records
sanitized usage even when the request or later processing fails. Conversation UUIDs
are preserved. Unpriced usage remains `unknown` with null cost; J7 does not invent tariffs.
Ledger and outbox rows are committed together before asynchronous delivery.

A separate lifespan task emits an applied snapshot revision/epoch/hash heartbeat every
15 seconds and flushes the outbox via the private HMAC endpoint. The transport supplies
an Idempotency-Key derived from the exact batch body. Lost ACKs retain event IDs and
retry with persisted backoff; the CRM deduplicates. Only an applied, unexpired snapshot
can be acknowledged. No valid configuration means no heartbeat claiming health.
The CRM ignores older heartbeats for the same instance and derives health at 45/90s,
checking revision, epoch and hash against the published configuration.

## J8 — probes e tarifa de referência

- `GET /integrations` exige HMAC de GET/path/corpo vazio. Retorna somente os cinco
  estados `configured|absent|error`; o mesmo resultado segue no heartbeat durável.
- Probes com cache monotônico de 60s, timeout de 3s e redirects desabilitados:
  OpenAI/Groq `GET /models`, Calendly `GET /users/me`; Google/n8n usa o contrato já
  existente `POST` com operação fixa de **leitura** `list`, intervalo de um minuto,
  calendar_id e fence do snapshot válido; nunca insert/patch/delete. Evolution usa
  a ponte privada CRM, que só consulta a instância já anexada. Configuração ausente
  resulta em absent; falha HTTP, timeout, fence ou autenticação em error. Configured
  indica sucesso da leitura, não prova de permissão de inferência/envio/criação.
- Corpos/headers/erros remotos são descartados; somente estados cruzam a API. Testes
  usam exclusivamente MockTransport e dados sintéticos. O sweep autouse verifica
  todas as respostas HTTP do runtime durante a suíte, inclusive erros, com canários
  de segredo/token/telefone/URL e padrões de token e telefone E.164; testes negativos
  provam que o detector rejeita vazamento.
- `vetorial_agent/pricing.json`, versão `2026-11-13.v1`, registra data, unidade,
  provider/model/tier/contexto e referências oficiais consultadas em 13/11/2026:
  [OpenAI API pricing](https://developers.openai.com/api/docs/pricing) (standard,
  contexto curto, input/cached/output) e [Groq Speech to Text](https://console.groq.com/docs/speech-to-text)
  (Whisper Turbo, USD/hora e mínimo faturável de 10s). Micro-USD inteiros.
  O catálogo acompanha o pacote instalado. Não estima tiers/contextos/cache writes
  não medidos e não transforma ausência de usage em zero. Novas chamadas carregam
  pricing_version; custo segue unknown/null, preservado por reinício/retry, para
  conciliação posterior com evidência completa e versão histórica. Nenhuma chamada
  paga é habilitada por este catálogo ou endpoint.

### J9 — contrato de operação no CRM (13/11/2026)

A ativação off/pilot/all e o binding são mutações HTTP no CRM, publicando revisão e
policy_epoch atômicos; o runtime continua consumindo o snapshot existente. All exige
nonce persistido de 60 s, uso único, ator/perfil/hash e frase `ATIVAR PARA TODOS` com
confirm booleano. Aceite exclusivamente sintético; nenhum modo real foi alterado.
Contrato e validações do expediente/binding/grant de responsável estão em
`_evo-i3-crm/docs/vetorial-integration-i3.md`, seção J9. Nenhum código executável do
runtime muda nesta etapa. Regressão completa com provedores dublês: 183 testes verdes.

## J10 — ponte de teste e autoridade única

CRM guarda owner/profile/revision/hash/plano/transcrição; nenhum ledger financeiro novo. POST HMAC `/test_sessions/manage` aceita create/show/close de esquema fechado; create fixa snapshot completo e papel, teto/amostra/turnos e UUID de sessão idempotente. `/test_sessions/:uuid/turns` usa apenas request_id UUID e texto; identidade vem da sessão autorizada por CRM e persistida no runtime. Orçamento é exclusivamente SandboxBudget, com escopos fixos frente/dia e operador/dia, tetos conservadores sem reembolso ao fechar, reserva durável por chamada e unknown após crash/erro.

`BridgeSandbox` é ligado por padrão ao app e só oferece simulação offline. `SimulationModel` reutiliza OpenAI.run para prompt/modelo/schema de ambos os papéis; substitui post sem possuir cliente HTTP, chave, mídia ou telemetria live. FakeCRM/FakeAgenda/NoopTransport continuam isolados. A estimativa reserva o contexto máximo suportado (262144 bytes/tokens conservadores incluindo snapshot/schemas e escaping), saída máxima configurada e tarifa versionada; o dublê completa uma rodada sem chamadas de ferramentas. Não é estimador para provedor pago: esse modo não existe nesta ponte. Configuração/tarifa/contexto sem limite confiável → 429 antes do modelo. Uso confirmado do dublê é zero; erro conserva unknown. Histórico de turnos completados persiste no plano, replay não repete modelo, nova tentativa unknown não solta reserva. Testes patcham transporte/provedor live, CRM, agenda e quota para falhar se alcançados, além de concorrência PostgreSQL e ensaio Rails→HTTP HMAC→runtime sintético.

## J11 — contrato fechado do snapshot

`vetorial_agent/snapshot.schema.json` é a cópia técnica byte a byte de `config/vetorial_snapshot.schema.json` no CRM I3, incluída no pacote Python para validação independente sem rede. Atualizar os dois arquivos juntos; a origem canônica é o CRM. O schema fecha blocos e tipos/enum/limites, inclusive a linguagem de ações/JSON Schema e referências exclusivamente locais. Campos opcionais ausentes preservam os defaults versionados; os payloads A14 completos continuam aceitos. Configuração inválida não renova lease; a política de lease de 120s permanece.

`tests/test_snapshot_contract.py` cobre campos desconhecidos/secret, tipos/tetos, referências remotas, contrato original A14, lista piloto e expediente sobreposto. A fixture de agenda usa 45min, igual ao contrato fixo do CRM; 30min é explicitamente recusado. Matriz de autorização/request/concorrência HTTP documentada em `docs/vetorial-integration-i3.md` no CRM; orçamento e deduplicação do sandbox continuam sob a autoridade runtime existente. Sem inferência paga.


## J12 — synthetic integrated acceptance and legacy adoption

Companion: `tests/rehearse_backend.py`, invoked by CRM
`spec/integration/vetorial_backend_rehearsal_spec.rb`. Uses real local HTTP/HMAC,
FastAPI ingress, PostgreSQL and a private Redis/Sidekiq queue. Model requests pass
through the actual OpenAI adapter with an offline HTTP double; WhatsApp typing/send
are doubles in Rails. The script has no control routes in the deployed application.
See CRM `docs/vetorial-integration-i3.md` §J12 and the original front delivery §J12
for commands, full acceptance matrix and rollback measurement.

`legacy_adoption.adopt_legacy_draft` is an explicit operator operation, never boot
fallback. Before calling it, drain all old runtime workers and retain their original
observed effective payload plus canonical hash. Compare that complete reviewed payload
to `cache.pin()` (only revision/epoch may differ; deployment/binding must match).
For each pending conversation, call:

```python
from vetorial_agent.legacy_adoption import adopt_legacy_draft
adopt_legacy_draft(store, conversation_id,
    observed_payload=reviewed_a14_payload,
    observed_sha256=reviewed_a14_hash,
    snapshot=cache.pin())
```

The locked operation refuses active execution/analysis leases, changed behavior,
wrong hash/version, an already managed draft from another revision and unresolved
local effects. It only adds the target fence to the existing legacy draft; it never
clears claims, pauses, queues, operations, receipts, unknown effects or attempts.
Already adopted identical drafts are no-ops. Reconcile sending/uncertain effects
before adoption, using CRM receipts; never mark them pending. Preserve original
observed files outside the runtime; do not read behavior env as startup fallback.

The start-first rehearsal leaves the old claim active while the new worker attempts
claim, then releases it gracefully, adopts the draft and resumes with zero additional
inference/send. Rollback keeps the adopted draft compatible with the legacy worker
(which ignores the added fence). Production I7 must still collect actual old/new
inventories and effective configuration; the synthetic fixture is not that evidence.
