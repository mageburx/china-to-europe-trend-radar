# China-to-Europe Trend Radar — Área privada (MVP v0.4)

## Estado

- Site público GitHub Pages: notícias, sinais e candidatos extraídos por palavras-chave (não prova de vendas).
- `private_dashboard.py`: app Streamlit autenticada, com propostas de fornecedores, preços de referência PT/ES e cenários de contribuição unitária. A app é código preparado **não está alojada/ligada** até criar um projeto Supabase, executar o SQL e fazer deploy.
- No estado inicial os formulários são manuais. Nem o 1688 nem o Douyin disponibilizam garantidamente preços e vendas via páginas públicas. O classificador automático só lê títulos de notícias da China que já entraram no RSS.

## Passo 1 — Criar Supabase

1. Abre https://supabase.com/dashboard e cria um projeto (região europeia quando disponível).
2. Vai a **SQL Editor** e executa o conteúdo de `supabase/schema.sql`. Cria duas tabelas com RLS por proprietário e revoga permissões ao papel `anon`.
3. Em **Authentication → Users**, cria/convida o utilizador que acederá ao dashboard. Configura registo público como desativado se não precisares de inscrições abertas.
4. Em **Project Settings → API Keys**, obtém **Project URL** e uma **publishable key** (ou legacy anon key). **Nunca uses secret ou service_role keys neste dashboard.**
5. Cada utilizador só lê/escreve as próprias linhas graças às políticas RLS; autenticação por email/palavra-passe da Supabase Auth.

## Passo 2 — Correr localmente

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements-private.txt
```

Cria `.streamlit/secrets.toml` localmente (nunca versionar):

```toml
SUPABASE_URL = "https://ID_DO_PROJETO.supabase.co"
SUPABASE_PUBLISHABLE_KEY = "sb_publishable_..."
```

Ou configura as mesmas variáveis de ambiente. Depois:

```bash
python -m streamlit run private_dashboard.py
```

## Passo 3 — Aceder no iPhone

- Abre https://share.streamlit.io/ e cria uma nova app a partir do repositório GitHub, branch `main`, main file path `private_dashboard.py`.
- O Streamlit Community Cloud deteta automaticamente `requirements.txt` na raiz do repositório, que já inclui o pacote `supabase`. Escolhe Python 3.11 ou uma versão compatível.
- Em **App settings → Secrets**, adiciona as duas chaves anteriores. Os secrets ficam no servidor do Streamlit Cloud, nunca no GitHub. Usa o URL de deploy para aceder no iPhone.
- A app exige login Supabase antes de consultar preços. O endereço Streamlit pode ser acessível publicamente, mas as tabelas privadas requerem autenticação, RLS e os grants previstos.
- Nunca publiques no GitHub credenciais, contratos ou preços privados. A `data/radar.sqlite` no repositório contém apenas observações públicas; o GitHub Actions limpa a tabela local de avaliações antes de cada commit. Atenção: Git conserva histórico, por isso nunca deves guardar dados privados no ficheiro versionado.

## Passo 4 — Como preencher o sistema

1. **Fornecedores**: regista link HTTPS, nome, MOQ, custo unitário e custos por lote.
2. **Preços concorrentes**: regista preços realmente observados em PT/ES, com URL e especificações equivalentes.
3. **Comparar margens**: introduz a previsão de preço e custos de vendas. A margem é cenário, não previsão.
4. **Pistas chinesas**: lê os produtos reconhecidos pelos termos específicos em títulos RSS. São apenas candidatos por verificar.

**Limitações:** a comparação ainda exige verificação manual de fornecedores, IVA, direitos, nomenclatura aduaneira, garantia, marcações, certificações, conformidade e concorrência. As taxas de IVA oferecidas (PT 23% e ES 21%) são exemplos de taxas normais e não aplicam tratamentos específicos nem regras de OSS/IVA transfronteiriço. Não efetuar compras baseado exclusivamente no score ou na simulação.

## Segurança e arquitetura

- `private_dashboard.py` usa apenas `SUPABASE_PUBLISHABLE_KEY` com Supabase Auth.
- As tabelas `public.radar_supplier_quotes` e `public.radar_competitor_prices` usam RLS `auth.uid() = owner_id` e permissões limitadas ao papel autenticado.
- O código não usa uma chave de serviço de bypass a RLS, não faz cache de clientes autenticados e não exporta propostas/preços para GitHub Pages.
- `radar.public_safety` limpa avaliações antigas do SQLite versionado antes do commit; isto **não** remove dados de commits históricos.
- Para continuidade de sessão entre atualizações do browser, será necessário evoluir a autenticação; nesta versão, uma atualização/reinício pode obrigar a novo login.

Documentação: https://supabase.com/docs/guides/database/postgres/row-level-security , https://supabase.com/docs/reference/python/auth-signinwithpassword