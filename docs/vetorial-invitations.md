# Convites e reenvio de usuários · N8

16/11/2026. Fonte: N1–N7 do acervo Vetorial - Evo CRM Agentes; N6 aprovada.
PRs auth #1 (9ceee23088a635d45569d1732842bd204273e975) e frontend #17
(bb4011a65d085a8bee512771024e1e51ebd7247a), incorporados antes da implantação.

## Configuração aprovada para implantação

Portainer permanece proprietário das stacks 75/76/83. Preservar Env, secrets,
redes, volumes, comandos e labels existentes. Alterar apenas imagem e as chaves
abaixo, pelo PUT da stack. Nunca publicar o compose integral, que contém secrets.

Auth e Sidekiq:

```yaml
image: ghcr.io/ricardoisus/evo-auth-service-community:vetorial-9ceee23@sha256:4b50874b512bbad348c947de67962e6cabd81c49bbd6b46389e4d340a4e06c46
environment:
  SMTP_PORT: '587'
  SMTP_AUTHENTICATION: plain
  SMTP_ENABLE_STARTTLS_AUTO: 'true'
  MAILER_SENDER_EMAIL: envio-automatico@agenciavetorial.com
  FRONTEND_URL: https://evo.agenciavetorial.com
  RUN_MIGRATIONS: 'false'
  USER_INVITATIONS_ENABLED: 'false'
  USER_INVITATIONS_RECIPIENT_ALLOWLIST: ricardohenrique@agenciavetorial.com
```

SMTP_SSL e SMTP_TLS permanecem ausentes. Banco sobrepõe ENV: corrigir somente
installation_configs.SMTP_AUTHENTICATION, login → plain, preservando o valor
anterior no backup privado. SMTP_PASSWORD_SECRET é valor direto, não path.
Nenhuma senha é alterada ou transferida para este documento.

Frontend:
`ghcr.io/ricardoisus/evo-ai-frontend-community:vetorial-bb4011a@sha256:17ba893a60fbc70d206856e6f74f774a7c3031b5296c2fd55d0de441b5e86b60`.

## Compatibilidade e ordem

O auth upstream avançou 110 commits sobre a imagem anterior. A estrutura de
users continua UUID. Migrações upstream de permissões não pertencem a esta
entrega: manter boot automático de migrações desligado nos dois processos e
aplicar **somente 20261116120001**, explicitamente, antes dos serviços. Não
executar db:migrate global, seeds ou reconciliação ampla de grants.

1. Renovar dump evolution_crm, backup Portainer DB, composes/Env/specs e digests.
2. Validar arquivo com pg_restore 16; restaurar em rede interna isolada, sem
   worker, saída SMTP ou portas; ensaiar up/up/down/up e preservação dos usuários.
3. Executar `rails db:migrate:up VERSION=20261116120001` na imagem nova,
   entrypoint explícito, mesmas variáveis/rede do auth; verificar registro e UUID.
4. Corrigir SMTP_AUTHENTICATION no auth, via runner sem logger, após backup.
5. Atualizar stacks 75/76/83 pela API Portainer, por digest. A feature permanece
   off nesta fase; recuperação comum fica funcional com SMTP corrigido.
6. Conferir réplicas 1/1, rollouts completed, ready/front 200 e bundle novo.
7. N9 habilita flag restrita a Ricardo, testa no máximo três e-mails, limpa
   somente fixture criada e registra recebimento/cabeçalhos ou pendência humana.

## Privacidade

Cloudflare Tunnel e50b44ad-ac8d-4d53-92a9-75d492cd4229 encaminha
`evo.agenciavetorial.com` diretamente a `http://evo_frontend:80`; Traefik não
é o ingress dessa rota. API Cloudflare confirma zero jobs Logpush da zona.
Nginx novo desativa access_log no prefixo reset; front isolado remove query,
usa no-referrer e não inicia analytics. OpenTelemetry no auth está desativado.
Não usar Traefik para esta rota sem antes suprimir sua query no access log:
sonda local sintética mostrou que ele registra query até em 404.

## Reversão

Backup: `/root/evo_crm/backups/convites-n8-20261116`, manager01, 0700/0600.
Arquivos 75/76/83-before.json guardam metadata/compose/Env; specs e dump ao lado.
Restaurar os três composes e Env pelo PUT Portainer, usando seus digests antigos.
Restaurar o valor anterior de SMTP_AUTHENTICATION (`login`) caso se reverta SMTP.
Manter migração aditiva em produção; nunca aplicar down nem restaurar dump por
rotina. Se já houve ativação N9: flag off, cancelar queued, aguardar sending,
invalidar tokens pending sob lock e preservar auditoria antes de trocar imagens.
Conferir 1/1, readiness/front 200 e configuração efetiva nos dois processos.
Ensaio em seco valida payload idêntico aos backups sem enviar PUT de reversão;
ensaio de down só no clone isolado. Não houve necessidade de rollback real.
