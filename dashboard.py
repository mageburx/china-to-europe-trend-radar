"""Streamlit dashboard. Run: python -m streamlit run dashboard.py"""
from datetime import datetime, timedelta, timezone
import pandas as pd
import streamlit as st
from radar.core import DEFAULT_DB, connection, seed_catalog, summaries

st.set_page_config(page_title="China → Europe Trend Radar",page_icon="📡",layout="wide")
st.title("📡 China → Europe Trend Radar")
st.caption("MVP v0.4 · Dashboard público (somente leitura) · Observações verificáveis ≠ procura ou vendas · Pontuação exploratória")

con = connection(DEFAULT_DB)
seed_catalog(con)
products = summaries(con)
count = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
last = con.execute("SELECT MAX(ingested_at) FROM observations").fetchone()[0]

m1,m2,m3,m4=st.columns(4)
m1.metric("Produtos monitorizados",len(products))
m2.metric("Menções recolhidas",count)
m3.metric("Elegíveis para revisão",sum(p["status"]=="Rever para amostra" for p in products))
m4.metric("Última recolha", last[:10] if last else "Ainda não")

if count==0:
    st.info("Sem observações recolhidas. Executa `python -m radar.collect` no terminal e atualiza esta página. Os produtos abaixo são apenas hipóteses de pesquisa.")

categories=sorted({p["category"] for p in products})
col1,col2=st.columns([1,2])
with col1:
    category=st.selectbox("Categoria", ["Todas"]+categories)
with col2:
    only_complete=st.toggle("Mostrar apenas avaliações completas",False)
filtered=[p for p in products if (category=="Todas" or p["category"]==category) and (not only_complete or p["complete"])]

if filtered:
    frame=pd.DataFrame([{"Produto":p["name"],"Categoria":p["category"],
        "China 7d":p["cn7"],"PT 7d":p["pt7"],"ES 7d":p["es7"],
        "Score parcial /100":p["score"],"Estado":p["status"]} for p in filtered])
    st.dataframe(frame,use_container_width=True,hide_index=True)
    st.subheader("Detalhe do produto")
    chosen=st.selectbox("Produto a analisar",[p["id"] for p in filtered],
                        format_func=lambda x:next(p["name"] for p in filtered if p["id"]==x))
    p=next(p for p in filtered if p["id"]==chosen)
    st.markdown(f"**Pontuação exploratória: {p['score']}/100** · **{p['status']}**")
    st.caption("Não é probabilidade de sucesso, previsão de vendas ou validação legal.")
    st.bar_chart(pd.DataFrame({"Pontos":p["components"]}))
    st.markdown(f"**Pesquisa CN:** `{p['cn_query']}` · **PT:** `{p['pt_query']}` · **ES:** `{p['es_query']}`")
    st.warning(f"Risco inicial: {p['risk_level']} — {p['notes']}. Necessária verificação de conformidade real antes de importar.")
    obs=pd.read_sql_query("""SELECT market, source, title, url, observed_at,
       metric_type, metric_value FROM observations WHERE product_id=?
       ORDER BY observed_at DESC LIMIT 150""",con,params=(chosen,))
    if not obs.empty:
        st.markdown("#### Histórico de evidência")
        obs["Dia"]=pd.to_datetime(obs["observed_at"],utc=True).dt.date
        hist=obs.groupby(["Dia","market"]).size().unstack(fill_value=0)
        st.line_chart(hist)
        st.dataframe(obs[["market","source","title","observed_at","url","metric_type","metric_value"]],
                     column_config={"url":st.column_config.LinkColumn("Fonte")},
                     hide_index=True,use_container_width=True)
    else:
        st.caption("Ainda não existem menções para este produto.")
    st.info("Este painel é só de leitura. Para fornecedores, margens e notas, utiliza private_dashboard.py com Supabase Auth.")
    if p["profit"] is not None:
        st.metric("Contribuição unitária estimada (sem IVA, antes de fixos e IRC/IRS)",f"{p['profit']:.2f} €")
        st.caption("Modelo simplificado: verificar IVA dedutível, direitos aduaneiros, tratamento dos portes, comissões e devoluções.")

st.divider()
st.subheader("🧭 Caixa de entrada — novos sinais da China")
st.caption("Notícias amplas de categorias de consumo; títulos por classificar. Não representam produtos nem tendências comerciais validadas.")
discovery=pd.read_sql_query("""SELECT category, source, title, url, observed_at
 FROM discoveries ORDER BY observed_at DESC LIMIT 100""",con)
if discovery.empty:
    st.info("A caixa de entrada ainda está vazia. Executa `python -m radar.discover` para procurar pistas em RSS públicos.")
else:
    st.dataframe(discovery,hide_index=True,use_container_width=True,
                 column_config={"url":st.column_config.LinkColumn("Artigo")})
st.divider()
st.markdown("**Fontes:** Google News RSS é um proxy de menções, não um ranking de vendas. Douyin, Xiaohongshu e 1688 só entram por importação manual verificada nesta versão. Google Trends API e integração de fornecedores são fases futuras.")
con.close()
