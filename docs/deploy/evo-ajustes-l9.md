# L9 — política de implantação

Autorização: cadeia Evo ajustes L9, Ricardo, 14/11/2026.
PRs CRM34 e frontend15 revisados L7/L8; merges b6f79da e45a6657.
Backup privado manager01: /root/evo_crm/backups/ajustes-l9-20261114.
Migração20261114040000 executada em contêiner descartável antes dos serviços, exit0.
Stacks77/78/83: preservar Env, secrets, volumes, redes, constraints e limites.
Trocar somente imagem por digest e acrescentar em deploy:

```yaml
update_config:
  order: start-first
  failure_action: rollback
  parallelism: 1
```

Usar PUT Portainer existente, Prune=false, PullImage=true. Sem stack deploy externo.
Reversão: inativar regra pela API CRM; restaurar compose de cada stack do backup via Portainer. Preservar tabelas/auditoria e dados novos; nunca restaurar dump por rotina.
Evidências/digests/resultados completos: acervo Vetorial - Evo CRM Agentes/entregas/L-ajustes.md §L9.
