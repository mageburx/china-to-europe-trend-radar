# 📡 China-to-Europe Trend Radar — MVP v0.1

Radar de **hipóteses de produtos de consumo** para estudar diferenças temporais entre menções na China (CN) e na Europa (PT/ES). **Não mede vendas, nem demonstra tendências comerciais.**

## O que funciona nesta versão

- Catálogo de oito produtos candidatos (exemplos de pesquisa, **não tendências identificadas**)
- Recolha RSS de menções no Google News, parametrizada para China / Portugal / Espanha
- Caixa de entrada de notícias chinesas por categoria (sinais a classificar, **não produtos identificados automaticamente**)
- Ingestão de observações documentadas manualmente (Douyin, Xiaohongshu, 1688, etc.), sem contornar autenticação nem restrições das plataformas
- Deduplicação persistente numa base SQLite local
- Comparação de menções nos últimos 7 dias com os 7 anteriores
- Score **exploratório** 0–100 que atribui zero aos dados comerciais em falta
- Dashboard Streamlit para avaliar evidência, concorrência, custo, margem e revisão de conformidade
- Workflow GitHub Actions diário (06:15 UTC) e acionamento manual, com atualização do HTML no repositório
- Exportação HTML autónoma (abre no navegador sem instalar Streamlit)
- CI para testes em cada push
- Testes de parser, pontuação, deduplicação e persistência

## Instalar (Windows/macOS/Linux)

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m radar.discover --max-items 5     # notícias de descoberta
python -m radar.collect --limit-products 2  # primeiro teste: 6 consultas
python -m streamlit run dashboard.py
```

Acede ao endereço indicado pelo Streamlit, geralmente `http://localhost:8501`.

### Dashboard HTML imediato, sem Streamlit

```bash
python -m radar.report
```

Abre `dashboard_offline.html` no navegador. É um **snapshot somente-leitura**; podes filtrar e consultar produtos e fontes, mas a avaliação manual é editável apenas na versão Streamlit. Para atualizar o HTML, volta a executar o comando após recolher dados.
Para recolher as 8 hipóteses em PT/ES/CN:

```bash
python -m radar.collect
```

**Sem acesso RSS:** o coletor regista erros e nunca insere exemplos falsos para esconder falhas. O dashboard funciona com zero observações.

## Importar sinais do Douyin / Xiaohongshu / 1688

Preenche o ficheiro `config/manual_observations_template.csv` (podes copiar para `data/minhas_observacoes.csv`). Cada linha exige um produto já existente no catálogo, mercado (`CN`, `PT`, `ES`), plataforma, título, URL verificável e data ISO 8601. Métricas de visualizações / encomendas só devem ser anotadas quando observadas diretamente e com unidades explícitas.

```bash
python -m radar.import_observations data/minhas_observacoes.csv
```

Exemplo de formato (não constitui um dado real):

```csv
product_id,market,source,title,url,observed_at,metric_type,metric_value
car_gap,CN,Douyin,Exemplo de observação,https://example.com/item,2026-11-06T12:00:00+00:00,views,100
```

## Como ler o score

| Dimensão | Pontos máx. | Evidência |
|---|---:|---|
| Menções CN nos últimos 7 dias e crescimento | 25 | Automática, proxy de interesse |
| Menções PT/ES 7 dias | 20 | Automática, proxy de interesse |
| Concorrentes diretos na UE | 15 | Auditados manualmente |
| Margem unitária estimada | 20 | Inputs manuais de custos e preço |
| Documentação / conformidade revista | 10 | Revisão manual, não certificação |
| Potencial de demonstração | 10 | Revisão manual |

O score **não é uma probabilidade de sucesso**. A etiqueta "Rever para amostra" só aparece quando há pelo menos 3 menções CN, 2 menções UE, auditoria da concorrência, custos/preço e conformidade revista (não substitui parecer técnico/legal). Se a revisão de conformidade bloquear o produto, o estado é "Bloqueado" mesmo que tenha score alto.

**Importação e IVA**: o cálculo usa preço de venda / 1,23 como aproximação da receita líquida (PT continental, taxa normal), presumindo uma operação com direito a dedução. O custo posto em Portugal deve incorporar custo de fabrico/compra, frete, direitos e desembaraço aplicáveis, excluindo IVA recuperável quando apropriado. Avaliar cada produto segundo regras europeias (GPSR e regimes específicos). Nunca confiar apenas numa marcação CE fornecida pelo vendedor.

## Fontes e limitações

- Google News RSS é **imprensa agregada**, não Douyin nem vendas chinesas.
- `CN` significa parametrização geográfica/idioma do agregador, **não geolocalização garantida dos autores**. Não é uma amostra estatística representativa.
- A versão alpha da API Google Trends requer acesso aprovado; não é utilizada.
- O MVP não extrai dados privados nem ignora CAPTCHAs / bloqueios; fontes sociais só via links registados manualmente.
- Os candidatos estão pré-definidos: a descoberta automática de novos produtos, tradução inteligente, procura direta no social commerce e comparação de marketplaces são **etapas seguintes**.
- A base SQLite é versionada diariamente via Actions como solução temporária de MVP. Isso aumenta o tamanho do histórico Git; para uso prolongado migrar para Supabase/PostgreSQL.
- O dashboard está preparado para **execução local**; o repositório não ativa automaticamente hospedagem pública nem oferece alertas.

## Arquitetura

```text
Google News RSS CN/PT/ES ──> radar.collect ─┐
Google News RSS amplo CN ──> radar.discover ──> caixa de entrada no dashboard
Observações manuais ────> import_observations ├──> SQLite ──> score_product ──> Streamlit
Catálogo CSV ────────────> seed_catalog ─────┘
GitHub Actions (diário) ────> collect ────────> commit SQLite (temporário)
```

## Próxima fase (após sinais reais)

1. Autodescoberta de novos produtos através de feeds consentidos/licenciados e classificação IA.
2. Integração 1688/fornecedores autorizados, dados de preços com histórico e estimativas de landed cost por HS code.
3. Confirmação de procura via dados de marketplaces e Google Trends API se aprovado.
4. Base central PostgreSQL, testes de qualidade de dados, alertas por Telegram/email e deploy autenticado.

## Referências oficiais

- [Google Trends API alpha](https://developers.google.com/search/apis/trends)
- [TikTok Creative Center](https://ads.tiktok.com/resources/help/article/creative-center)
- [Regras de conformidade de importadores na UE](https://europa.eu/youreurope/business/product-rules-compliance/general-product-compliance/index_en.htm)
- [Regulamento de Segurança Geral de Produtos (GPSR)](https://eur-lex.europa.eu/eli/reg/2023/988/2026-05-29)

## Privacidade e licenciamento

Não inserir informações pessoais, credenciais, cookies ou tokens em CSVs nem no repositório. Projeto técnico inicial, sem aconselhamento aduaneiro ou jurídico.

## Criar repositório no GitHub

1. Criar no GitHub um repositório **privado e vazio** chamado `china-to-europe-trend-radar` (não marcar README/licença/.gitignore).
2. A partir da pasta do projeto, no terminal:

```bash
git init
git branch -M main
git add .
git commit -m "feat: initial market trend radar MVP"
git remote add origin https://github.com/mageburx/china-to-europe-trend-radar.git
git push -u origin main
```

Se o GitHub exigir autenticação, usa o GitHub CLI ou um método autenticado suportado pela tua conta. O ficheiro `.github/workflows/collect.yml` passa a correr diariamente e pode ser executado via separador Actions. O repositório precisa de permitir ao GitHub Actions permissão `Read and write` para commits de dados. Atenção: `data/radar.sqlite` contém dados de pesquisa e, nesta versão, fica versionado no Git — nunca colocar dados pessoais lá.

## Registos de contexto incluídos no snapshot inicial

Para que o dashboard offline tenha fontes verificáveis, incluímos três **artigos publicados** sobre tendências setoriais chinesas nas áreas de animais, acessórios automóveis e viagens. São rotulados `Pesquisa de contexto`, não contam como menções a produtos, **não alimentam os scores** e não demonstram a existência de uma oportunidade de importação. Foram recolhidos por pesquisa manual, não pelo coletor RSS automatizado.

- 01/10/2026 — [iiMedia: consumo e produtos inteligentes para animais](https://www.iimedia.cn/c400/118002.html)
- 30/09/2026 — [GEP Research: acessórios automóveis](https://www.gepresearch.com/81/view-1222194-1.html)
- 11/10/2026 — [People's Daily/Economic Daily: padrões de consumo em viagens](https://finance.people.com.cn/n1/2026/1011/c1004-40812285.html)

**Estado dos testes neste ambiente:** os testes unitários do sistema, armazenamento e pontuação passaram. A saída RSS ao vivo e a instalação do Streamlit não puderam ser testadas aqui por falta de acesso de rede; devem ser verificadas no GitHub Actions/PC.
