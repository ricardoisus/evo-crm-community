# Vetorial agente

- Leia README.md; siga AGENTS.md do monorepo e o plano da frente Evo CRM Agentes.
- Serviço fechado por padrão. Não enviar a contatos nem criar eventos reais em testes.
- Prompts de implantação em vetorial_agent/prompts; origem e hashes em prompt-manifest.json. Revisão editorial canônica na pasta agentes/ da frente. Sincronizar ambos em A3.
- Testes: `python -m pytest tests -q`. Postgres opcional apenas no banco sintético vetorial_agent_a12_test.
- Evals: `python -m evals.run --output DIRETORIO --media-dir evals/media`. Chave somente no ambiente do processo. `--replace` repete explicitamente casos já salvos.
- Nunca copiar a pasta fontes/ privada. Credenciais de produção só por Swarm secrets.
- A12 termina em PR. Não mesclar, implantar ou ativar nesta etapa.
