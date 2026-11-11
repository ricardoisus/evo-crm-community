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
