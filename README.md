# China-to-Europe Trend Radar — MVP v0.4

Radar experimental de hipóteses de produtos de consumo. Compara **menções em notícias** na China, Portugal e Espanha. **Não mede vendas nem garante tendências comerciais.**

## Estado

- **Dashboard público online:** https://mageburx.github.io/china-to-europe-trend-radar/
- **Código do backoffice privado:** [private_dashboard.py](private_dashboard.py) — ainda requer Supabase e deploy Streamlit.
- **Configuração completa do privado:** [README-private.md](README-private.md).
- **Testes:** `python -m pytest -q`.
- **Recolha:** GitHub Actions, agendada às 06:15 UTC (a execução pode atrasar).

## O que está implementado

| Parte | Como funciona | Limitações |
|---|---|---|
| Notícias CN/PT/ES | Google News RSS por produto | Menções, não vendas |
| Descoberta CN | RSS de categorias chinesas, depois regras de termos específicos | Leads não verificados, sem volume comercial |
| Links Douyin/Xiaohongshu/1688/Taobao | Metadados de URLs públicas previamente selecionadas | Não é pesquisa massiva e não extrai encomendas |
| Pontuação 0–100 | Score conservador de menções + evidência comercial | Não é probabilidade de sucesso; dados em falta ficam a zero |
| Dashboard público | Site HTML estático GitHub Pages | Não tem dados privados |
| Dashboard comercial privado | Streamlit + Supabase Auth + RLS por utilizador | Requer criar projeto Supabase, configurar credenciais e alojar |
| Comparação de preços | Propostas e benchmarks PT/ES introduzidos com URL verificável | Inputs manuais, preços não extraídos automaticamente |
| Simulador de margem | Custo unitário, MOQ, frete, encargos, IVA, marketing, comissões | Cenário, não validação fiscal/aduaneira |

## Arquitetura

```text
RSS China/Portugal/Espanha ────────────────┐
RSS amplo chinês -> candidate_discovery ───┼─> SQLite público
Links sociais públicos previamente curados ┘       │
                                                    └─> GitHub Pages (sinais NÃO comerciais)

Supabase Auth (email/password) ──> Supabase PostgreSQL (RLS) ──> private_dashboard.py
                                      │
                              Propostas de fornecedores e preços UE
                                      └─> Simulação de margem privada
```

O classificador automático apenas identifica termos concretos em **títulos de notícias chinesas**. Novos leads são guardados como `unverified`, e **não entram na pontuação de procura**.

## Executar o radar local

```bash
python -m venv .venv
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m radar.discover
python -m radar.candidate_discovery
python -m radar.collect
python -m radar.platform_collect
python -m radar.public_safety
python -m radar.report --public
python -m streamlit run dashboard.py
```

O `dashboard.py` é **somente leitura**. O HTML estático `dashboard_offline.html` pode ser aberto no navegador. Para o app comercial privado:

```bash
python -m streamlit run private_dashboard.py
```

Este último precisa de `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` e do schema em [supabase/schema.sql](supabase/schema.sql). Segue [README-private.md](README-private.md).

## Dados públicos e privados

**Nunca inserir preços negociados, contactos, contratos, dados pessoais, tokens ou notas sensíveis em `data/radar.sqlite` ou em CSVs versionados no GitHub.** O repositório é público e o SQLite também está versionado. A recolha executa `radar.public_safety` antes de commitar, como defesa adicional, mas dados enviados para commits antigos permanecem no histórico Git.

No Supabase, as tabelas `radar_supplier_quotes` e `radar_competitor_prices` usam RLS por `auth.uid()`. A chave publishable/anon não ultrapassa RLS. **Nunca usar secret/service_role keys na aplicação web.**

## Importar observações e recolher URLs públicas

- `config/products.csv`: catálogo e palavras-chave CN/PT/ES.
- `config/platform_links.csv`: links HTTPS de publicações escolhidas, com `product_id,url`.
- `config/manual_observations_template.csv`: modelo para observações com fonte, data e métrica explícita.
- `python -m radar.import_observations ficheiro.csv`: importa observações verificáveis.

URLs inacessíveis são ignoradas; não há bypass de login, CAPTCHA, ou inferências de vendas com base em metadados. O momento de verificação de uma página chinesa **não** é a data da publicação.

## Próximos incrementos

1. Criar o projeto Supabase, executar o schema e fazer deploy da área privada em Streamlit Community Cloud.
2. Testar sourcing: links de fornecedores com especificações iguais, provas de preço e custos.
3. Adicionar fontes de dados sociais/comerciais licenciadas e APIs autorizadas para medir crescimento de procura.
4. Migrar histórico de RSS para armazenamento persistente sem depender de SQLite versionado.

## Fontes técnicas

- [Supabase — RLS](https://supabase.com/docs/guides/database/postgres/row-level-security)
- [Supabase Auth — Python](https://supabase.com/docs/reference/python/auth-signinwithpassword)
- [Streamlit Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud)
- [Regras gerais de segurança de produtos na UE](https://europa.eu/youreurope/business/product-rules-compliance/general-product-compliance/index_en.htm)

**Nota:** investigação comercial exploratória. Não fazer encomendas sem conformidade técnica, segurança, impostos, logística e procura confirmados.
