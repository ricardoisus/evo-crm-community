# Vetorial agente

Data: 12/11/2026. Etapa: A4, continuação da A12. Autor: Theo, CTO · Codex Sol principal.
Status: candidato corrigido em A4; implantação e ativação bloqueadas até a bateria e os requisitos de agenda/identidade estarem aprovados. Sem implantação do runtime.
Fontes: `../../AGENTS.md`; frente `Vetorial - Evo CRM Agentes/00-plano.md`, `entregas/K1-canal-e-arquitetura.md`, `entregas/C2-canal-anexar.md`; prompts A3 v1.1 (analista P3 v1.0) e convenções comerciais da Vetorial.

Serviço próprio decidido na K1. Recebe a outbox autenticada do CRM e responde com a identidade AgentBot. Nunca usa o roteamento global do bot ou o webhook genérico sem persistência. Requer o PR complementar do CRM.

| Requisito | Implementação |
|---|---|
| Persistência | ACK após commit; eventos únicos; Postgres próprio; operações com chaves estáveis; recuperação por lease |
| Rajada | 20 s desde o último texto; 30 s se último áudio; versão monotônica; rechecagem local e no CRM antes de cada bloco e após cada pausa de digitação |
| Humano | Celular/Evo/atribuição pausam de forma durável. Remover responsável não retoma. Reaplicar `ia-ativa`, com conversa sem responsável, retoma explicitamente |
| Estado visível | Etiquetas `ia-em-atendimento` e `ia-pausada` em conversa e negócio do pipeline configurado; motivo em `custom_attributes.vetorial_ia` |
| Mídia | Download autenticado do anexo da própria conversa; até 20 MiB; 10 anexos/contexto; áudio transcrito, visão e PDF até 40 páginas; falha vira `media_unavailable` |
| Histórico | Paginação com cursor composto, máximo 1000 mensagens; privado excluído do contexto de atendimento; sem threads/Assistants |
| Saída | JSON validado; até 5 blocos de 300 caracteres; digitação 1500 caracteres/min, até 12 s/bloco; emoji único; silêncio cria nota privada e pausa |
| Agenda | Free/busy Google, expediente explícito, janela de 14 dias, 45 min, fuso São Paulo, Meet e convite; consulta expira em 10 min; rechecagem antes da mutação; evento vinculado à conversa |
| Analista | 5 min após a última atividade; resumo, campos, etiquetas, sugestão em nota privada. Nunca move SQL nem outras etapas automaticamente |
| Proteções | Caixa fixa, lista vazia nega tudo, equipe bloqueada; 20 saídas/h e 3 ações mutáveis de agenda/h por conversa; até 3 tentativas por versão, inclusive análise |

Decisão A12: usar somente Postgres para claims e locks, inclusive lock da agenda. Substitui o Redis apenas como mecanismo de timer/lock desenhado na K1. Há uma única autoridade durável e nenhum novo consumidor no Redis compartilhado. Serviço roda uma réplica, atualização `stop-first`.

O endpoint CRM deduplica a criação do Message no mesmo commit. O envio ao WhatsApp passa a `sending` em transação anterior ao POST. Uma queda/timeout nessa janela vira `uncertain`, pausa e exige reconciliação. Nunca se promete exactly-once no transporte externo. O lock de canal, igual ao da U8, permite registrar `source_id` antes de seu eco ser classificado como humano.

Campos existentes de origem desconhecida/humana são preservados. O agente só corrige valor que ele próprio havia escrito e que continua igual ao anterior. `false` não é ausência. Pipeline precisa de ID explícito; sem ele, informações permanecem no estado da conversa/nota, sem criar negócios por dedução.

## Controles A4

- `OPENAI_PROJECT` é obrigatório. O adaptador não escolhe um projeto por conta própria. Configurar o projeto aprovado no template.
- `VETORIAL_AGENT_ANALYSIS_ALL=true` em CRM e runtime permite somente análise privada de contatos válidos da caixa fixa, excluindo equipe e bloqueados. Padrão `false` conserva a lista restrita.
- A permissão do analista nunca autoriza `send`, `typing`, ferramentas do atendente ou agenda. `analysis_media` permite apenas ler anexos da própria conversa para análise. Grupos/status não entram na análise.
- Atendimento mantém allowlist explícita; vazia nega todos, mesmo com analista amplo. Não existe valor curinga `*` nem controle de ativação geral de um clique nesta versão. Esse controle continua pendente antes de liberação geral.
- Evals: `--workers 4` paraleliza somente conversas sintéticas independentes; default 1. O serviço de produção continua com uma réplica e processamento sequencial.
- Erros externos registram somente status e códigos de uma lista fixa, sem payload. Resumo sem resposta pública deixa a latência do primeiro bloco vazia, em vez de inventar zero.
- O check de identidade detecta a regressão C26 da A2. Ferramentas obrigatórias são verificadas no atendimento, sem contar o analista posterior. Aprovação automática não substitui revisão editorial.

## Agenda e credencial

`agenda.py` aplica política, autorização, oferta, lock e recibos; `deploy/calendar-gateway.n8n.json` é somente um gateway Google restrito, inativo. OAuth permanece no n8n. Não é um runtime de atendimento.

A4 deve configurar no gateway o calendar ID original da agenda Trabalho | Ag. Vetorial e uma credencial Header Auth própria (`X-Vetorial-Calendar-Key`). Selecionar a credencial Google existente após reconexão. O JSON não contém segredos e não foi importado no n8n. Desativar gravação de execuções está configurado no artefato. Não reutilizar tokens do fluxo legado.

O gateway fixa calendário, domínio Google, operações, duração, Meet e `sendUpdates=all`. Não recebe URL/calendar ID do LLM. Datas aceitas: hoje, amanhã, esta/essa semana, próxima semana, próximos dias e intervalo ISO. Pedido não reconhecido retorna erro para esclarecer; não inventa disponibilidade.

Agendamento tem ID estável por conversa, inclusive após timeout. Remarcação faz PATCH, preserva evento; cancelamento só alcança evento próprio, pelo vínculo e e-mail. Não apaga o evento para remarcar. E-mail isolado nunca autoriza acesso cruzado. O lock evita conflitos entre instâncias do agente; alteração manual simultânea no Google continua uma janela externa a reconciliar.

## Validação

```sh
uv venv .venv
uv pip install --python .venv/bin/python -e '.[test]'
.venv/bin/python -m pytest tests -q
# POSTGRES_TEST_URL é opcional; banco deve ser vetorial_agent_a12_test, exclusivamente sintético.
```

Fixtures de mídia em `evals/media` não contêm dados reais. A bateria chama OpenAI e substitui CRM/WhatsApp/agenda por stubs. As transcrições e a comparação dos dois modelos estão na entrega A1 da frente. Falhas editoriais continuam registradas para A2/A3; aprovação automática não equivale à aprovação da Helena.

Capturas inspecionadas: [pipeline](docs/pipeline-estados.png), [conversa pausada](docs/conversa-pausada.png). Frontend local K1, dados sintéticos; não são capturas da implantação desta etapa.

## Candidato para A4

1. Revisar e incorporar os dois PRs; corrigir comportamento em A3 e sincronizar prompts/manifesto. Não ativar com falhas de escopo/agenda pendentes.
2. Revalidar a agenda, janelas e telefone pessoal; lista vazia permanece fechada. Selecionar pipeline comercial real. Não adivinhar IDs/telefone.
3. Fazer backup de banco, stacks/compose, env e digests conforme `docs/vetorial-upgrade.md` e SSOT. Portainer continua dono das stacks.
4. Criar banco `vetorial_agente` e roles próprias. Migrar com role DDL antes dos serviços (`python -m vetorial_agent migrate`). Role do runtime recebe somente CONNECT/USAGE/SELECT/INSERT/UPDATE nas suas tabelas; não acessa `evolution_crm`.
5. Migrar CRM aditivamente (`20261112030000`) antes de atualizar web/Sidekiq. Não editar/recriar banco existente.
6. Publicar imagens amd64 imutáveis no GHCR e fixar digests. `deploy/stack.template.yml` é um template deliberadamente incompleto, sem porta pública e sem credenciais reais.
7. CRM: configurar `VETORIAL_AGENT_INBOX_ID`, `VETORIAL_AGENT_PIPELINE_ID`, `VETORIAL_AGENT_BOT_ID`, `VETORIAL_AGENT_URL`, lista e equipe; segredo compartilhado por arquivo Swarm; manter `VETORIAL_AGENT_SEND_ENABLED=false` até homologar. AgentBot serve como identidade, sem vínculo nativo que ative delegação genérica.
8. Runtime: secrets de banco/OpenAI/HMAC/gateway, URLs internas, modelo escolhido, mesmo inbox/lista/equipe e expediente. Registrar labels na UI com cores legíveis. Não alterar Evolution/Rabbit/AMH/Typebot/Mautic.
9. Verificar réplicas 1/1, `/ready` 200, CRM/readiness e frontend 200. Só Ricardo inicia teste no seu número. Separar implantação de evento real.

Para assumir: enviar como humano ou atribuir a pessoa. Para devolver: remover responsável e reaplicar `ia-ativa`. Reaplicar significa remover a etiqueta antiga, se houver, e adicioná-la novamente. Agente não retoma ao chegar nova mensagem.

Para interromper/reverter: desabilitar envios no CRM, zerar réplicas do runtime no Portainer e restaurar digests anteriores registrados no backup A4. Preservar tabelas/outbox, conversas, canal e eventos. Não restaurar dump sobre dados novos nem apagar volumes. `sending/uncertain` exige conciliar com `source_id`/eco observado antes de qualquer retomada manual. Nunca mudar `uncertain` para `pending` por rotina.

## Documentação de API usada

- [OpenAI Responses e function calling](https://developers.openai.com/api/docs/guides/function-calling), `store=false` e ferramentas estritas.
- [Modelos](https://developers.openai.com/api/docs/models) e [preços](https://developers.openai.com/api/docs/pricing), consultados em 11/11/2026.
- [Calendar events.insert](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert), `conferenceDataVersion=1` e `sendUpdates=all`.
- [n8n Webhook Header Auth](https://docs.n8n.io/integrations/builtin/credentials/webhook/).

## A7: bateria limitada e áudio Groq

Continuação em branch `codex/evo-agentes-a7-bateria`, sem merge/deploy. Snapshots A6 v1.2 e manifesto sincronizados. A transcrição usa exclusivamente Groq `whisper-large-v3-turbo`, `language=pt`. O limite existente de 20 MiB fica abaixo do corte legado de 23 MB; arquivos maiores seguem recusados, sem ampliar escopo. Chave por `GROQ_API_KEY_FILE`; A9 deve cadastrar o secret `vetorial_agent_groq`, ainda não criado.

Executor: `python -m evals.a7 --output DIRETORIO --phase probe`, depois `--phase sample`; inspecionar a amostra antes de `--phase full --leader MODELO`. Somente Luna/Sol. Medidor persistente inclui analista e mídia, reserva custo antes de cada chamada e soma tokens retornados às tarifas oficiais Standard. Teto US$0,50 OpenAI e US$0,10 Groq. Pendência de uso por falha de transporte impede reinício/retry cego. BudgetStop escapa aos handlers do worker. Não apagar ou zerar o medidor para continuar uma etapa.

`--phase render` gera conversas legíveis sem chamar API nem ler credenciais. Transcrições parciais são preservadas se houver bloqueio. Medição usa relógio real para API e digitação até o primeiro envio no stub; não comprova transporte WhatsApp. Seis resultados da amostra são reaproveitados no lote do líder na mesma versão.
