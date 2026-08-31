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
