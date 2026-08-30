# Diretrizes do Codex

## Fluxo obrigatório de mudanças

- Toda alteração de código, configuração versionada, infraestrutura ou documentação deve ser feita em uma branch dedicada, com commits claros e um Pull Request para revisão antes do merge e do deploy.
- Não fazer commits diretamente na branch `main` e não considerar uma alteração concluída apenas porque ela foi aplicada localmente ou diretamente no servidor.
- Antes de alterar produção, confirmar que a mudança correspondente está versionada e passou pelo fluxo branch → commit → PR → merge → deploy, exceto em mudanças emergenciais autorizadas.
- Mudanças operacionais ou emergenciais feitas diretamente em produção devem ser exceção, ter autorização explícita e ser registradas imediatamente; assim que possível, criar a mudança equivalente em branch, commit e PR para evitar divergência de configuração.
- Ao finalizar uma tarefa, informar a branch, os commits, o Pull Request e o estado do deploy. Se alguma dessas etapas não tiver ocorrido, declarar isso explicitamente e explicar o motivo.
- Não criar, aprovar, mesclar ou fazer deploy de um Pull Request sem respeitar as autorizações e proteções configuradas no repositório.
