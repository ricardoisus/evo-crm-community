# Vetorial agente

- Leia README.md; siga AGENTS.md do monorepo e o plano da frente Evo CRM Agentes.
- Serviço fechado por padrão. Não enviar a contatos nem criar eventos reais em testes.
- Prompts de implantação em vetorial_agent/prompts; origem e hashes em prompt-manifest.json. Revisão editorial canônica na pasta agentes/ da frente. Sincronizar ambos em A3.
- Testes: `python -m pytest tests -q`. Postgres opcional apenas no banco sintético vetorial_agent_a12_test.
- Evals pagos: `python -m evals.a7 --output DIRETORIO --phase probe`, depois `sample` e somente após inspeção `full --leader MODELO`. Teto persistente, sem Astra. O executor antigo só regenera resumo offline e recusa jobs novos. `--phase render` gera conversas sem API/credenciais.
- Nunca copiar a pasta fontes/ privada. Credenciais de produção só por Swarm secrets.
- A12 termina em PR. Não mesclar, implantar ou ativar nesta etapa.
