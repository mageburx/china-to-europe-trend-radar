"""Private commercial workspace backed by Supabase Auth + per-user RLS.

Streamlit runs server-side; this app does not use service-role keys. The public
GitHub Pages dashboard is separate and never receives private records.
"""
from __future__ import annotations

import csv
import os
import sqlite3
from pathlib import Path

import streamlit as st

from radar.commerce import estimate_unit_economics, evidence_url
from radar.core import CATALOG, DEFAULT_DB

st.set_page_config(page_title='Trend Radar · Área privada', page_icon='🔐', layout='wide')
st.title('🔐 China → Europe | Área comercial privada')
st.caption('Fornecedores, benchmarking e cenários de margem — armazenamento privado por utilizador.')


def setting(name: str) -> str:
    value = os.getenv(name, '')
    if value:
        return value
    try:
        return str(st.secrets.get(name, '')).strip()
    except (FileNotFoundError, KeyError):
        return ''


url = setting('SUPABASE_URL')
key = setting('SUPABASE_PUBLISHABLE_KEY') or setting('SUPABASE_ANON_KEY')
if not (url.startswith('https://') and key):
    st.error('Supabase não configurado. Define SUPABASE_URL e SUPABASE_PUBLISHABLE_KEY nos Secrets do Streamlit.')
    st.stop()

try:
    from supabase import create_client
except ImportError:
    st.error('Dependência em falta. Instala requirements-private.txt (supabase).')
    st.stop()

# Do not cache Supabase clients globally: authenticated sessions must be isolated.
client = create_client(url, key)


def store_session(session) -> None:
    st.session_state['private_auth'] = {
        'access_token': session.access_token,
        'refresh_token': session.refresh_token,
    }


session_data = st.session_state.get('private_auth')
if not session_data:
    st.info('Acesso restrito. Cria o utilizador em Supabase Authentication; o registo público é desativado neste MVP.')
    with st.form('login'):
        email = st.text_input('Email').strip()
        password = st.text_input('Palavra-passe', type='password')
        submitted = st.form_submit_button('Entrar')
    if submitted:
        try:
            resp = client.auth.sign_in_with_password({'email': email, 'password': password})
            if not resp.session:
                raise ValueError('Credenciais inválidas')
            store_session(resp.session)
            st.rerun()
        except Exception:
            st.error('Autenticação não concluída. Confirma o email e a palavra-passe.')
    st.stop()

try:
    response = client.auth.set_session(session_data['access_token'], session_data['refresh_token'])
    if not response.session:
        raise ValueError('Sessão expirada')
    store_session(response.session)
    # Server-side check, not based only on unverified local session data.
    user = client.auth.get_user().user
    if not user:
        raise ValueError('Identidade não validada')
except Exception:
    st.session_state.pop('private_auth', None)
    st.warning('A sessão terminou. Volta a entrar.')
    if st.button('Voltar ao login'):
        st.rerun()
    st.stop()

header_left, header_right = st.columns([5, 1])
header_left.caption(f'Ligado como {user.email or user.id} · As linhas são protegidas por RLS no Supabase.')
if header_right.button('Terminar sessão'):
    try:
        client.auth.sign_out({'scope': 'local'})
    except Exception:
        pass
    st.session_state.pop('private_auth', None)
    st.rerun()


def catalog() -> dict[str, str]:
    with Path(CATALOG).open(encoding='utf-8-sig', newline='') as file:
        return {r['id']: r['name'] for r in csv.DictReader(file)}


products = catalog()
product_ids = list(products)
if not product_ids:
    st.error('Catálogo vazio.')
    st.stop()


def fetch_rows(table: str) -> list[dict]:
    # RLS at the database level restricts these rows to auth.uid().
    return client.table(table).select('*').order('checked_on', desc=True).limit(500).execute().data or []


def show_local_leads() -> None:
    st.subheader('Pistas automáticas de produtos')
    st.caption('Correspondências de termos em títulos de notícias chinesas. Não são resultados de vendas.')
    if not DEFAULT_DB.exists():
        st.info('Sem base RSS local. Executa a recolha via GitHub Actions.')
        return
    try:
        with sqlite3.connect(f'file:{DEFAULT_DB}?mode=ro', uri=True) as con:
            con.row_factory = sqlite3.Row
            rows = [dict(row) for row in con.execute('''SELECT label_pt, matched_cn, title, url, published_at
                FROM candidate_leads ORDER BY published_at DESC LIMIT 80''')]
        if rows:
            st.dataframe(rows, hide_index=True, use_container_width=True)
        else:
            st.info('Ainda sem pistas classificadas.')
    except sqlite3.Error:
        st.info('As pistas surgirão após a execução do classificador automático.')


tabs = st.tabs(['Fornecedores', 'Preços concorrentes', 'Comparar margens', 'Pistas chinesas'])

with tabs[0]:
    st.subheader('Registar proposta de fornecedor (EUR)')
    st.caption('O preço deve ser confirmado numa fonte real. Não importar sem rever conformidade e qualidade.')
    with st.form('supplier'):
        product = st.selectbox('Produto', product_ids, format_func=lambda x: products[x])
        name = st.text_input('Fornecedor / loja', max_chars=200)
        supplier_url = st.text_input('Link de prova da oferta', placeholder='https://...')
        a,b,c = st.columns(3)
        unit_cost = a.number_input('Custo unitário (€)', min_value=0.0, value=2.0, step=0.5)
        qty = b.number_input('Quantidade/MOQ', min_value=1, value=100, step=1)
        freight = c.number_input('Frete total lote (€)', min_value=0.0, value=50.0)
        d,e = st.columns(2)
        imports = d.number_input('Direitos, desalfandegamento e outros custos (€)', min_value=0.0, value=0.0)
        compliance = e.selectbox('Estado de conformidade', ['unchecked','review','cleared','blocked'])
        notes = st.text_area('Notas privadas', max_chars=1500)
        if st.form_submit_button('Guardar proposta'):
            try:
                client.table('radar_supplier_quotes').insert({
                    'product_id': product, 'supplier_name': name.strip(),
                    'supplier_url': evidence_url(supplier_url),
                    'unit_cost_eur': unit_cost, 'order_quantity': qty,
                    'freight_total_eur': freight, 'import_cost_total_eur': imports,
                    'compliance_status': compliance, 'notes': notes,
                }).execute()
                st.success('Proposta guardada na base privada.')
            except Exception:
                st.error('Não foi possível guardar. Confirma campos, acesso e se o SQL do Supabase já foi executado.')
    try:
        quotes = fetch_rows('radar_supplier_quotes')
        if quotes:
            st.dataframe([{k:v for k,v in row.items() if k not in ('owner_id',)} for row in quotes],
                         hide_index=True, use_container_width=True)
        else:
            st.info('Ainda não há propostas reais de fornecedores.')
    except Exception:
        quotes=[]
        st.warning('A tabela privada de fornecedores não está acessível; verifica a configuração SQL.')

with tabs[1]:
    st.subheader('Registar preço de referência europeu')
    st.caption('Preço de consumidor com IVA incluído; é apenas uma observação, não prova de vendas.')
    with st.form('benchmark'):
        product = st.selectbox('Produto', product_ids, format_func=lambda x: products[x], key='price_prod')
        market = st.selectbox('Mercado', ['PT','ES'])
        merchant = st.text_input('Loja / marketplace')
        source_url = st.text_input('URL de prova', placeholder='https://...')
        retail_price = st.number_input('Preço publicado com IVA (€)', min_value=0.0, step=1.0)
        note = st.text_area('Notas / especificações', max_chars=1500)
        if st.form_submit_button('Guardar preço'):
            try:
                client.table('radar_competitor_prices').insert({
                    'product_id':product, 'market':market, 'merchant':merchant.strip(),
                    'evidence_url':evidence_url(source_url),
                    'retail_price_gross_eur':retail_price, 'notes':note,
                }).execute()
                st.success('Preço guardado na base privada.')
            except Exception:
                st.error('Não foi possível guardar o preço. Confirma campos e a configuração SQL.')
    try:
        prices = fetch_rows('radar_competitor_prices')
        if prices:
            st.dataframe([{k:v for k,v in row.items() if k not in ('owner_id',)} for row in prices],
                         hide_index=True, use_container_width=True)
        else:
            st.info('Sem preços europeus documentados.')
    except Exception:
        prices=[]
        st.warning('A tabela privada de preços não está acessível; verifica a configuração SQL.')

with tabs[2]:
    st.subheader('Comparar cenários económicos')
    if not quotes:
        st.warning('Introduz pelo menos uma proposta real de fornecedor na primeira secção.')
    else:
        chosen = st.selectbox('Proposta de fornecedor', list(range(len(quotes))),
            format_func=lambda i: f"{products.get(quotes[i]['product_id'], quotes[i]['product_id'])} — {quotes[i]['supplier_name']}")
        q = quotes[chosen]
        st.caption(f"MOQ {q['order_quantity']} un. · preço unitário {q['unit_cost_eur']} € · conformidade: {q['compliance_status']}")
        candidates = [p for p in prices if p['product_id']==q['product_id']]
        if candidates:
            st.dataframe([{'mercado':p['market'], 'loja':p['merchant'],
                            'preço com IVA':p['retail_price_gross_eur'], 'data':p['checked_on']}
                           for p in candidates], hide_index=True)
            default_price = float(candidates[0]['retail_price_gross_eur'])
        else:
            st.info('Sem referência europeia documentada para este produto. Introduz um preço-alvo hipotético.')
            default_price=25.0
        price = st.number_input('Preço de venda simulado com IVA (€)', min_value=0.01, value=max(0.01, default_price))
        vat = st.selectbox('Taxa de IVA para o cenário', [0.23,0.21], format_func=lambda x: 'Portugal 23%' if x==0.23 else 'Espanha 21%')
        x,y,z,w = st.columns(4)
        fulfilment = x.number_input('Expedição/un. (€)', min_value=0.0, value=3.0)
        fees = y.number_input('Comissões/un. (€)', min_value=0.0, value=2.0)
        marketing = z.number_input('Publicidade/un. (€)', min_value=0.0, value=5.0)
        returns = w.number_input('Devoluções/un. (€)', min_value=0.0, value=1.0)
        try:
            r = estimate_unit_economics(q['unit_cost_eur'],q['order_quantity'],
                q['freight_total_eur'],q['import_cost_total_eur'],price,vat,
                fulfilment,fees,marketing,returns)
            a,b,c = st.columns(3)
            a.metric('Custo posto em Portugal/un.', f"{r['unit_landed_eur']:.2f} €")
            b.metric('Contribuição estimada/un.', f"{r['contribution_eur']:.2f} €")
            c.metric('Margem contribuição', f"{r['contribution_margin_pct']}%")
            st.caption(f"Preço de equilíbrio com IVA: {r['break_even_gross_eur']:.2f} €. {r['assumptions']}")
            if q['compliance_status']!='cleared':
                st.warning('Conformidade não aprovada. O cálculo não constitui autorização ou recomendação para importar.')
        except ValueError as exc:
            st.error(str(exc))

with tabs[3]:
    show_local_leads()

st.caption('Segurança: Supabase Auth + RLS por proprietário. GitHub Pages não inclui dados comerciais. Não colocar chaves secretas no repositório.')
