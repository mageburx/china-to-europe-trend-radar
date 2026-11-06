"""Conservative per-unit scenario comparisons. NOT purchase or tax advice."""
from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlsplit


def evidence_url(value: str) -> str:
    """Validate an evidence link; does not fetch it or assert it is genuine."""
    url = str(value).strip()
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port:
        raise ValueError('É necessário um link HTTPS de fonte verificável.')
    return url


def _num(value, name: str) -> Decimal:
    try:
        num = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f'Valor inválido em {name}') from exc
    if not num.is_finite() or num < 0:
        raise ValueError(f'{name} não pode ser negativo ou infinito')
    return num


def estimate_unit_economics(
    unit_cost_eur, order_quantity, freight_total_eur, import_cost_total_eur,
    retail_price_gross_eur, vat_rate=0.23, fulfilment_eur=0,
    payment_fee_eur=0, acquisition_eur=0, returns_reserve_eur=0,
) -> dict:
    """Indicative scenario, assumes VAT recovery and commercially viable product.

    Total import cost: duties + customs clearance + other direct import costs,
    excluding recoverable input VAT. No tax handling is automated.
    """
    q = _num(order_quantity, 'quantidade')
    if q != q.to_integral_value() or q < 1:
        raise ValueError('A quantidade deve ser um inteiro positivo.')
    vat = _num(vat_rate, 'IVA')
    if vat >= 1:
        raise ValueError('IVA deve ser uma taxa decimal (ex.: 0.23).')
    unit = _num(unit_cost_eur, 'preço de compra unitário')
    freight = _num(freight_total_eur, 'transporte')
    import_total = _num(import_cost_total_eur, 'importação')
    gross = _num(retail_price_gross_eur, 'preço ao consumidor')
    costs = sum((_num(value, key) for key, value in {
        'entrega': fulfilment_eur,
        'comissões': payment_fee_eur,
        'aquisição': acquisition_eur,
        'devoluções': returns_reserve_eur,
    }.items()), Decimal('0'))
    landed = unit + (freight + import_total) / q
    revenue = gross / (Decimal('1') + vat)
    margin_eur = revenue - landed - costs
    ratio = margin_eur / revenue if revenue > 0 else None
    def money(x): return float(x.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
    return {
        'unit_landed_eur': money(landed),
        'net_revenue_eur': money(revenue),
        'contribution_eur': money(margin_eur),
        'contribution_margin_pct': round(float(ratio * 100), 1) if ratio is not None else None,
        'break_even_gross_eur': money((landed + costs) * (1 + vat)),
        'assumptions': 'Custos estimados; IVA recuperável; não inclui despesas fixas nem imposto sobre lucro.'
    }
