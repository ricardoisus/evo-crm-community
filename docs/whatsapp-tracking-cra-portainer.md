# Observação do tracking nativo da CRA no Portainer

O n8n continua como único emissor de produção. O [runbook da revisão Rails](https://github.com/ricardoisus/evo-ai-crm-community/blob/d6dcf6c9d50793d16045be27fc9b0262f24d8d4a/docs/whatsapp-tracking-cra.md) descreve as regras, os importadores e o contrato da observação.

| Stacks do Portainer | Revisão integrada | Tag GHCR |
| --- | --- | --- |
| `02_evo_crm` e `03_evo_crm_sidekiq` | [Rails PR 48](https://github.com/ricardoisus/evo-ai-crm-community/pull/48) | `ghcr.io/ricardoisus/evo-ai-crm-community:vetorial-d6dcf6c` |
| `08_evo_frontend` | [Frontend PR 32](https://github.com/ricardoisus/evo-ai-frontend-community/pull/32) | `ghcr.io/ricardoisus/evo-ai-frontend-community:vetorial-b4fb10e@sha256:da4d9851bb935871fc78e349ddaf094a597f45e9da58b511a0447b977f83a989` |

As três stacks são gerenciadas no Portainer. Aplique as mudanças abaixo sobre o compose retornado pela API de cada stack. O `docker-compose.swarm.yaml` da raiz é um modelo de instalação. Use manifestos GHCR `linux/amd64` fixados por digest. Antes do deploy, salve o compose, as variáveis da stack e o digest atualmente em execução em backup restrito. Confirme que nenhuma stack mudou desde a leitura usada para preparar a atualização.

## Imagens e migrações

1. Faça o backup do banco CRM e confira as migrations pendentes. Aplique as sete migrations do PR Rails pela stack web com a revisão integrada. Valide que as sete versões estão em `schema_migrations` e que o web está saudável antes de atualizar o Sidekiq para o mesmo digest.
2. Atualize a imagem do frontend após confirmar a saúde do Rails e do Sidekiq. Preserve os comandos, as redes, as rotas, as políticas de atualização, as configurações e as demais imagens existentes.

## Configuração e observação

1. Carregue as credenciais com as referências do runbook, usando o cofre. Disponibilize a chave de deduplicação e as credenciais Meta nos dois serviços Rails, o token de borda no web e a credencial de leitura de anúncios no Sidekiq. Use somente a caixa WhatsApp anexada à CRA. Mantenha os valores das credenciais e o ID da caixa fora do Git.
2. Execute `whatsapp_tracking:configure_cra` em ensaio. Confirme a caixa anexada, a regra ativa da CRA e a etapa Pré-Qualificado. Repita com `WHATSAPP_TRACKING_COMMIT_CONFIGURATION=true` e confira os dois destinos e as seis regras publicadas.
3. Exporte o estado do site pelo SQL somente leitura da revisão Rails. Passe o JSON por stdin ao importador `whatsapp_tracking:import_cra_website`. Confira as contagens no ensaio, aplique com `WHATSAPP_TRACKING_COMMIT_WEBSITE=true` e descarte o snapshot restrito.
4. Defina `WHATSAPP_TRACKING_OBSERVE_INBOX_IDS` com somente a caixa CRA no web e Sidekiq. No web, defina `WHATSAPP_TRACKING_CRA_SITE_ENABLED=true` e `WHATSAPP_TRACKING_CRA_SITE_MODE=observe`. Nos dois serviços mantenha `WHATSAPP_TRACKING_DELIVERY_ENABLED=false` e vazias as variáveis `WHATSAPP_TRACKING_DELIVERY_INBOX_IDS` e `WHATSAPP_TRACKING_CRA_PRODUCTION_OWNER_INBOX_ID`.
5. Valide o endpoint novo com entrada sintética, incluindo revogação, e confira que a entrada CRM de observação retorna 202 sem criar contato ou card. Valide a CAPI separadamente com um evento sintético e código de Test Events. Verifique o isolamento da Vetorial, a saúde dos três serviços e a ausência de envios de produção do Evo.
6. Compare os recibos de observação disponíveis com o legado por ID de evento, origem, nome, destino, horário e estágio. Registre origens ainda sem amostra. Preserve o destino atual da borda e os consumidores e agendas do n8n. A virada e o uso de leads reais dependem de autorização separada.

Para recuar, desligue o endpoint novo e limpe a lista de caixas observadas no web e no Sidekiq. Se necessário, restaure as imagens e configurações pelo backup do Portainer. Preserve o banco e os recibos para análise.
