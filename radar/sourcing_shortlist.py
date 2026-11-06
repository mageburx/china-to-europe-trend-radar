"""Research queue, NOT an opportunity score or import decision."""
from __future__ import annotations
from radar.core import summaries as product_summaries
from radar.public_market_offers import summaries as public_offer_summaries
from radar.wholesale_watch import wholesale_summaries
from radar.ebay_prices import price_summaries


def shortlist(con, max_items=8):
    if not 1<=max_items<=10:
        raise ValueError('Máximo 10 produtos')
    products=product_summaries(con)
    public=public_offer_summaries(con)
    wholesale=wholesale_summaries(con).get('offers',[])
    ebay=price_summaries(con)
    ebay_by_id={x['product_id']:x for x in ebay}
    result=[]
    for p in products:
        x=[o for o in public if o['product_id']==p['id']]
        cn_links=sum(o['market']=='CN' and o['status'] in ('title_only','price_observed') for o in x)
        cn_prices=sum(o['market']=='CN' and o['status']=='price_observed' for o in x)
        eu_prices=sum(o['market']=='PT' and o['status']=='price_observed' for o in x)
        w=[o for o in wholesale if o['product_id']==p['id']]
        cn_links+=sum(o['status'] in ('title_only','price_observed') for o in w)
        cn_prices+=sum(o['status']=='price_observed' for o in w)
        ebay_row=ebay_by_id.get(p['id'],{})
        eu_prices+=int(ebay_row.get('status')=='ok' and (ebay_row.get('listing_count') or 0)>0)
        # Research priority is the amount of traceable evidence, not a purchase signal.
        research_points=min(10,p['cn7']+p['eu7'])+min(2,cn_links)*3+min(2,cn_prices)*3+min(2,eu_prices)*3
        result.append({'id':p['id'],'name':p['name'],'category':p['category'],
            'research_points':research_points,
            'cn_news_mentions':p['cn7'], 'eu_news_mentions':p['eu7'],
            'cn_pages':cn_links,'cn_list_prices':cn_prices,'eu_list_prices':eu_prices,
            'equivalent_specs_verified':False,
            'supplier_moq_verified':False,
            'shipping_import_cost_verified':False,
            'eu_compliance_verified':False,
            'status':'Pesquisa comercial pendente',
            'next_step':('Confirmar dimensões e quantidade por embalagem em CN e PT/ES' if cn_prices and eu_prices
                         else 'Obter preços públicos ou cotações com prova documental')})
    result.sort(key=lambda x:(-x['research_points'],-x['cn_news_mentions'],x['id']))
    return result[:max_items]
