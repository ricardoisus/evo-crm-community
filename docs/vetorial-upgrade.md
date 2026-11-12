# Vetorial upgrade baseline

This fork tracks `evolution-foundation/evo-crm-community` and uses the stable
`v1.0.0` family pointers as its baseline. The production Swarm previously ran
images created on 2026-04-25 plus runtime patches applied to minified assets.

## Pipeline automation delivered by upstream

- stage-level trigger/action rules;
- inactivity actions for pipeline stages;
- move a conversation between pipelines while preserving the item;
- Journey nodes for Create Pipeline Task, Move to Pipeline Stage, Assign to
  Pipeline and Send Canned Response;
- Pipeline Stage Changed trigger and pipeline-stage conditions;
- automation execution logs and deduplication;
- redesigned pipeline list, board and card detail UI.

## Vetorial customizations

- Sienna `#B45417` / Sienna Dark `#8C4011` primary accent;
- Ivory `#F2EFE8` surfaces, Charcoal `#0E0E0E` dark background, Dark Teal
  `#1A4F4F` and Steel Blue `#55768A` supporting accents;
- accessible horizontal composer menu with dark-mode support;
- WhatsApp-like selection toolbar (bold, italic, strike, code, ordered list,
  bullet list and quote);
- Instagram Reel Open Graph preview stored as external metadata, with no Reel
  file download into ActiveStorage.

## Production invariants

- `/root/infra-ssot.md` is authoritative before every deployment;
- `evolution_crm` is shared by Auth and CRM and must never be recreated;
- Portainer must remain the owner of the ten existing Evo stacks;
- official family services must be upgraded together to `v1.0.0`;
- custom CRM/frontend images are published to GHCR with immutable
  `vetorial-<sha7>` tags;
- database, Portainer compose and current image digests must be backed up before
  the upgrade;
- `ACTIVE_STORAGE_SERVICE`, `RAILS_INBOUND_EMAIL_SERVICE`, a stable CRM
  encryption source (`ENCRYPTION_KEY` or the existing `SECRET_KEY_BASE`) and
  `EVO_AI_CRM_URL` must be present before booting `v1.0.0`.

## Deal pipeline deployment — 2026-08-31

- Portainer stacks `02_evo_crm`, `03_evo_crm_sidekiq` and `08_evo_frontend`
  were updated through the Portainer API, preserving Portainer ownership.
- CRM and CRM Sidekiq run
  `ghcr.io/ricardoisus/evo-ai-crm-community:vetorial-cd67891` pinned to digest
  `sha256:59406983de307ebc7bab894fa225803b8a217111bee1d18aa40f320ae79fced2`.
- Frontend runs
  `ghcr.io/ricardoisus/evo-ai-frontend-community:main-9bc1b03` pinned to digest
  `sha256:ebadfd29f5746c20d07aa94b0a13ab620348ed6f788fe8ef1e773fe76971bdec`.
- The additive migration `20260829120000` completed before CRM Sidekiq and the
  frontend were updated. Production checks confirmed the canonical UUID
  `scheduled_actions.deal_id`, the bigint `legacy_deal_id`, deal association
  tables, UTM defaults and migrated deal associations.
- The pre-deploy database, Portainer database, compose files and image list are
  stored under
  `/root/evo_crm/backups/deal-pipeline-20260831-121643` on the Swarm manager.
- Post-deploy checks confirmed all three services at `1/1`, internal and public
  readiness HTTP 200, frontend HTTP 200 and a clean browser render of the login
  page.

## Interface U6 release candidate — 2026-11-11 (not deployed)

- CRM [PR #26](https://github.com/ricardoisus/evo-ai-crm-community/pull/26)
  merged as `83d6a4eeda914c57ff7b8fea66058747ad86bb0e`; frontend
  [PR #13](https://github.com/ricardoisus/evo-ai-frontend-community/pull/13)
  merged as `783a9511320f41f21bc2a8b2ec2e9d4b184aa43c`.
- Local validation: 37 CRM examples, 24 frontend tests, TypeScript/build,
  focused lint and browser regression (304px, both themes, desktop/mobile,
  persistence, failure rollback, keyboard, loading/retry) passed.
- U5 I1 correction was inapplicable: this fork uses one installation account in
  `RuntimeConfig.account` (a Hash), with no `account_id` in the custom definition
  table, including production. Adding the proposed predicate would cause SQL
  failures. The existing installation scope is retained and tested for the
  contact/deal-only catalog and private pipeline access. I2 rejects mixed PATCH
  payloads with 422; I3–I6 remove repeated lookups and collection catalog queries.
- **Deployment blocked before migration or service updates:** dispatching
  `publish-vetorial.yml` on CRM main repeatedly returned HTTP 422,
  `Actions has been disabled for this repository.` Permissions report
  `enabled=true, allowed_actions=all` and workflow state is `active`.
  Explicit workflow enable and permissions disable/enable did not restore runs;
  original permissions were restored. No CRM image for this merge was produced.
- Frontend publishing run:
  https://github.com/ricardoisus/evo-ai-frontend-community/actions/runs/43119473155.
  Completed successfully; immutable candidate
  `ghcr.io/ricardoisus/evo-ai-frontend-community:vetorial-783a951@sha256:ad3c967a2eed8761ce4333fe9a45a4daab09f0d19e5094e15565346fd5622d62`.
  Do not deploy the frontend alone: its API needs the new CRM migration/code.
- Private pre-deploy backup: `/root/evo_crm/backups/interface-u6-20261111`
  on manager01 (0700 directory/0600 files). It contains a verified custom-format
  dump of `evolution_crm` (`pg_restore -l`), Portainer DB, the three Portainer
  composes and service specifications, rollback image digests and SHA256 manifest.
  Refresh this backup immediately before any later deployment.
- Current production/rollback CRM and Sidekiq:
  `ghcr.io/ricardoisus/evo-ai-crm-community@sha256:892f9e265a19ac38ed3a262b8b7a4b52d89cb25764e1671a70c8f8115a5a9cfd`.
  Frontend:
  `ghcr.io/ricardoisus/evo-ai-frontend-community@sha256:89c46a856e2ab3d5edb4bea96b2a1033d3ce3f1c3894f54c1e765a9959b58997`.
- Resume: repository owner checks the CRM Actions banner and re-enables runs if
  offered; otherwise submits the 422/enabled/active evidence to GitHub Support.
  Theo then publishes the merged CRM/frontend via CI, records both GHCR digests,
  refreshes backup, runs only additive migration `20261112010000` before services,
  updates Portainer stacks 77/78/83 pinned to digests, and checks 1/1, readiness,
  frontend 200 and the new served bundle. Never recreate `evolution_crm`.
- Rollback after a future attempt: update stacks 77/78/83 through Portainer with
  the saved previous digests/compose; keep the additive columns, verify 1/1 and
  health again. No rollback or database restore is needed for this U6 attempt:
  production was not modified.
- Current production baseline checks (no deployment): CRM/Sidekiq/frontend 1/1,
  internal readiness/frontend 200 and public readiness/frontend 200 (curl with
  browser User-Agent). Current served bundle remains `index-CrSO_VVX.js` and
  `index-BVTcnHuF.css`; the new candidate bundle was not served in production.
- Local synthetic OAuth session was revoked after browser checks; Docker Desktop
  was stopped, restoring its pre-K1 state. No production credentials were rotated.
- Ricardo's 2-minute authenticated check after the future deploy: open a board,
  inspect fixed columns and independent scroll, open Personalizar cartões,
  change visibility/order/density, save/reload, check a second pipeline and a
  narrow viewport. Use no messages or conversation read actions for this check.

## Pipeline interface deployed — 2026-11-11 (U7)

- This completes and supersedes the U6 deployment blocker above. CRM Actions
  returned HTTP 422 despite enabled repository permissions; the approved U7
  procedure built the merged commit outside Actions. Future CRM images in this
  chain follow this procedure until Actions is available.
- Source: CRM main merge `83d6a4eeda914c57ff7b8fea66058747ad86bb0e`
  (CRM PR26), frontend merge `783a9511320f41f21bc2a8b2ec2e9d4b184aa43c`
  (frontend PR13), component pointers already merged by monorepo PR7.
- Clean CRM worktree; `docker buildx build --platform linux/amd64
  -f docker/Dockerfile --build-arg RAILS_ENV=production
  --build-arg BUNDLE_WITHOUT=development:test
  --build-arg RAILS_SERVE_STATIC_FILES=true
  --build-arg GIT_SHA=83d6a4eeda914c57ff7b8fea66058747ad86bb0e
  --provenance=mode=max --sbom=true
  --tag ghcr.io/ricardoisus/evo-ai-crm-community:vetorial-83d6a4e --push .`.
  Same production arguments as `publish-vetorial.yml`; the mutable `vetorial`
  alias was not moved. Local Docker credential helper stalled; an ephemeral
  GHCR-only helper retrieved `gh auth token` in memory, without persisting it.
- Portainer stacks 77/78 now use
  `ghcr.io/ricardoisus/evo-ai-crm-community:vetorial-83d6a4e@sha256:11304de006796934ee29aa27d8f562103a4db4978197847d9393f9713b6ff49d`.
  AMD64 manifest: `sha256:c27bbca39a13ae3184d6df66b16f3e2b4a55a9d422573d7954379a2bd39840b1`.
- Stack83 now uses
  `ghcr.io/ricardoisus/evo-ai-frontend-community:vetorial-783a951@sha256:ad3c967a2eed8761ce4333fe9a45a4daab09f0d19e5094e15565346fd5622d62`,
  published by the successful frontend U6 workflow.
- Renewed private backup on manager01:
  `/root/evo_crm/backups/interface-u7-20261111` (0700/0600). Includes database
  dump, Portainer DB, stack metadata/compose before and after, service specs
  and SHA256 manifest. `pg_restore -l` verified the fresh `evolution_crm` dump.
- Before updating services, a disposable container of the new CRM image on
  worker01 ran `bundle exec rails db:migrate:up VERSION=20261112010000`,
  sharing the existing CRM network and receiving its environment in memory.
  Exit0; schema_migrations contains the version, pipelines now has JSONB
  `card_configuration` NOT NULL/default `{}` and integer
  `card_configuration_revision` NOT NULL/default0. No database recreation.
- Updated CRM, then Sidekiq, then frontend through Portainer's stack PUT API,
  preserving Env and all compose content except image references. All stacks
  remain readable/editable; no Portainer password reset was needed.
- All three rollouts completed, replicas1/1. Public readiness and frontend
  HTTP200; internal checks recorded in the U7 delivery. Initial public502
  during service startup resolved before acceptance. Browser login page
  rendered at1440x900, zero page errors; new bundle `index-O8yGzbZ1.js` /
  `index-BWWe-Rv6.css`. No authenticated production mutation or message test.
- Application rollback: use Portainer PUT for stacks77/78/83 with saved
  pre-deploy composes/Env. CRM+Sidekiq previous image:
  `ghcr.io/ricardoisus/evo-ai-crm-community@sha256:892f9e265a19ac38ed3a262b8b7a4b52d89cb25764e1671a70c8f8115a5a9cfd`;
  frontend previous image:
  `ghcr.io/ricardoisus/evo-ai-frontend-community@sha256:89c46a856e2ab3d5edb4bea96b2a1033d3ce3f1c3894f54c1e765a9959b58997`.
  Keep additive columns and new data; do not restore the dump routinely.
  Verify completed rollouts,1/1 and health/frontend200. No rollback was needed.
- Ricardo's2-minute check: open a pipeline, inspect equal columns and independent
  scroll; open **Personalizar cartões**, change a field/order/density, save and
  reload; check another pipeline and a narrow viewport. Do not open conversations
  or send messages for this check. Authenticated production acceptance remains
  pending Ricardo; synthetic regression was already completed by U6.
- Evolution/channel/agent/calendar integrations unchanged; next stage is
  `evo-agentes:C2`. Docker Desktop was closed before U7 and is closed again at
  its end; local volumes and other stages' worktrees are preserved.

## Vetorial existing Evolution instance — 2026-11-11 (U8)

The CRM now attaches the existing `vetorial` instance through
`Evolution::AttachExistingInstance` and the authenticated
`/webhooks/evolution_attached` callback. Native destructive authorization is
blocked for this instance. The origin proxy for `chat.agenciavetorial.com`
continues to target the separate Chatwoot service; no Chatwoot integration moved.

- CRM PRs27–30 are merged. Final source commit:
  `65d5615862c090a412a284f818b6f8cd5482ac3d`.
- Stacks77/78 were updated through Portainer, changing only the image:
  `ghcr.io/ricardoisus/evo-ai-crm-community:vetorial-65d5615@sha256:98fed57fa26d6f3b8fea12646a2429b22522666d1cf00ec41d3917473b6406bd`.
- Image built outside Actions with the workflow arguments, amd64, provenance
  mode=max and SBOM. No database migration. Frontend remains the U7 image.
- Backup: `/root/evo_crm/backups/canal-u8-20261111` on manager01, private
  permissions, PostgreSQL dump verified with pg_restore, Portainer DB,
  compose/environment snapshots, service specs and SHA256 manifest.
- Rollouts completed, CRM/Sidekiq/frontend1/1, internal/public readiness and
  frontend HTTP200. All old web/worker tasks were gone before attachment.
- Box: `702de859-a676-4af9-a2a1-806da82785d8`, “Vetorial · WhatsApp”.
  Attachment active at `2026-11-12T00:26:05Z`. No bot enabled, no sends or reads.
- Evolution GET comparison: only Vetorial's previously null webhook changed.
  Chatwoot/Rabbit/Typebot/settings remain equal to K1. Other11 instances match
  all K1 configuration hashes. Connection remains open. No history imported.
- Validation:32 RSpec examples, lint on new application files, committed local
  synthetic observations with no automatic sends. Production awaits a natural
  first event; configuration is not proof of real delivery.

Rollback: first run the attachment service's detach operation (see the CRM
`docs/attach-existing-evolution.md`), which verifies ownership, disables only
this webhook and retains the box/messages/instance. Then restore77/78 via
Portainer to the original compose/environment from the backup, CRM digest
`sha256:11304de006796934ee29aa27d8f562103a4db4978197847d9393f9713b6ff49d`.
Do not restore a dump over new data or delete the instance/box. Confirm1/1 and
HTTP200. Rollback was prepared, not executed.

A1 must explicitly consume the inert observed-message path; generic Agent Bot
callbacks are suppressed. Human phone echoes carry
`content_attributes.evolution_origin=human_device`. Reconcile outgoing CRM IDs
and concurrent provider echoes in A1's idempotent output implementation.
Operational evidence: Vetorial activity repository,
`Vetorial - Evo CRM Agentes/entregas/C2-canal-anexar.md` and its sanitized JSONs.

## Agentes Vetorial — A14, 2026-11-12

CRM PR31 e runtime PR10→11→12→13 incorporados em main (739a1f0/6512d2f).
Controles finais: CRM PR32 (7d55b91), runtime PR14 (b848276).
Imagens geradas fora do Actions, com provenance/SBOM, tag imutável e digest:

- CRM/Sidekiq: `ghcr.io/ricardoisus/evo-ai-crm-community:vetorial-7d55b91@sha256:470656fe2180abee288d8b2b071af8d14bd4c272baa0c926fc53475d7335bb3b`.
- Runtime: `ghcr.io/ricardoisus/evo-crm-community/vetorial-agente:vetorial-b848276@sha256:823b52eb9c60aec69a1a402b6f50f60a1704eedd60b3257dfe93206ab8bc6db3`.

Configuração de produção sem segredos em `services/vetorial-agente/deploy/stack.production.json` e `crm-production.json`.
Aplicar pelo Portainer, stacks 77/78 e nova `10_vetorial_agente`, rede `network_public`.
Criar identidade AgentBot sem AgentBotInbox e preencher somente seu UUID em CRM.
Bootstrap dedicado em `deploy/bootstrap.sql`; migrar CRM20261112030000 e banco
vetorial_agente antes dos serviços, com stacks temporárias Portainer e restart none.
Role de aplicação recebe apenas CONNECT/USAGE/SELECT/INSERT/UPDATE; role DDL perde LOGIN após migração.
Secrets Swarm por arquivos; nunca incluir valores em compose ou argumentos.

Backup privado no manager01: `/root/evo_crm/backups/agentes-a14-20261112` (dump evolution_crm,
n8n_hfer3, Portainer DB, composes/specs/digests anteriores; conferir SHA256 e pg_restore).
Calendly HTTP400 de conexão externa em A14: disponibilidade fecha sem inventar horários.
Google alvo `ricardohenrique@agenciavetorial.com`; gateway n8n versionado com Header Auth
própria e credencial Google existente, sem executar criação real. Piloto só Ricardo;
atendimento e analista Luna, analysis_all/all_contacts false.

Reversão: `agent-control.py off`, restaurar composes77/78 do backup pelo Portainer
(CRM65d5615 digest98fed57fa26d6f3b8fea12646a2429b22522666d1cf00ec41d3917473b6406bd),
preservar tabelas/outboxes/eventos e manter runtime em zero réplicas. Desativar somente
o gateway novo, sem alterar credencial Google ou workflows existentes.
Não restaurar dump sobre dados novos. Resultado operacional e prova de eventos reais
na entrega canônica `Vetorial - Evo CRM Agentes/entregas/A14-implantacao.md`.

A14 concluída em 12/11: stack102 criada pelo Portainer; ponte, CRM e Sidekiq ativos
somente para o telefone pessoal autorizado, ambos os controles amplos false. Migração
CRM20261112030000 aplicada; sete tabelas no banco dedicado, aplicação sem DDL/DELETE,
role DDL sem LOGIN. Stacks temporárias100/101 e secret efêmero DDL removidos.
Runtime/CRM/frontend e readiness públicos200; entrada runtime sem HMAC401.
Gateway n8n `0A8x1SP5IXvHctuq` ativo: free/busy200 com calendário correto, list200,
get de ID inexistente404, sem chave403. Zero evento criado.

Correção de transporte n8n2.10.3: comparação isolada comprovou que `sendBody`
dinâmico perde o corpo (Google400 Missing timeMin); o mesmo caso com true literal
retorna200. Gateway usa IF e dois nós HTTP com switches literais; teste Node em
`deploy/calendar-gateway.test.mjs`. Artefato final incorporado pelo PR18 (1576de9),
sem mudança no código da imagem runtime b848276. PRs16/17 registram a investigação;
o diagnóstico temporário foi removido do artefato final.

Sem novos eventos naturais na janela A14: logs de contato fora da lista e resposta
real do agente ainda não foram observados. Eventos pré-runtime permanecem13 incoming
com ID do provedor. Gate/configuração e testes comprovam restrição; não equivalem
à validação do primeiro atendimento. Calendly continua indisponível por conexão
externa; Ricardo reconecta Google no Calendly e inicia o teste pelo celular.
