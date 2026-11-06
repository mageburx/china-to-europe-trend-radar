"""Streamlit dashboard. Run: python -m streamlit run dashboard.py"""
from datetime import datetime, timedelta, timezone
import pandas as pd
import streamlit as st
from radar.core import DEFAULT_DB, connection, seed_catalog, summaries, save_assessment

st.set_page_config(page_title="China → Europe Trend Radar",page_icon="📡",layout="wide")
st.title("📡 China → Europe Trend Radar")
st.caption("MVP v0.1 · Observações verificáveis ≠ procura ou vendas · Pontuação exploratória")

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
    a=p["assessment"]
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
    st.markdown("#### Avaliação manual da oportunidade")
    st.caption("Os campos financeiros, concorrência e conformidade não são inferidos de notícias. Introduz apenas valores verificados.")
    with st.form("assessment"):
        l,r=st.columns(2)
        with l:
            competition=st.number_input("Concorrentes diretos na UE (0 = nenhum encontrado)",
                min_value=0,value=int(a.get("competition_eu_count") or 0),step=1)
            known_comp=st.checkbox("Concorrência auditada",value=a.get("competition_eu_count") is not None)
            price=st.number_input("Preço de venda c/IVA (€)",min_value=0.0,
                value=float(a.get("consumer_price_eur") or 0),step=0.5)
            landed=st.number_input("Custo unitário posto em PT (€)",min_value=0.0,
                value=float(a.get("landed_cost_eur") or 0),step=0.5)
            known_price=st.checkbox("Preço e custo verificados",value=a.get("consumer_price_eur") is not None and a.get("landed_cost_eur") is not None)
        with r:
            delivery=st.number_input("Entrega/embalagem por unidade (€)",min_value=0.0,value=float(a.get("delivery_eur") or 0),step=0.5)
            marketing=st.number_input("Aquisição de cliente (€)",min_value=0.0,value=float(a.get("marketing_eur") or 0),step=0.5)
            fees=st.number_input("Taxas/comissões (€)",min_value=0.0,value=float(a.get("fees_eur") or 0),step=0.5)
            returns=st.number_input("Provisão devoluções (€)",min_value=0.0,value=float(a.get("returns_eur") or 0),step=0.5)
            compliance=st.selectbox("Revisão de conformidade (não é certificação)",
                ["pending","review","cleared","blocked"],
                index=["pending","review","cleared","blocked"].index(a.get("compliance_status") or "pending"),
                format_func=lambda x:{"pending":"Pendente","review":"Em revisão","cleared":"Documentação revista","blocked":"Bloqueado"}[x])
            visual=st.slider("Potencial de demonstração (avaliação manual)",0,10,int(a.get("visual_score") or 0))
        notes=st.text_area("Notas e evidências",value=a.get("notes") or "")
        saved=st.form_submit_button("Guardar avaliação")
    if saved:
        if known_price and (price<=0 or landed<=0):
            st.error("Introduz preço e custo maiores do que zero, ou desmarca 'verificados'.")
        else:
            save_assessment(con,chosen,competition_eu_count=int(competition) if known_comp else None,
                consumer_price_eur=float(price) if known_price else None,
                landed_cost_eur=float(landed) if known_price else None,
                delivery_eur=float(delivery),marketing_eur=float(marketing),fees_eur=float(fees),
                returns_eur=float(returns),compliance_status=compliance,
                visual_score=visual,notes=notes)
            st.success("Avaliação guardada localmente.")
            st.rerun()
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
