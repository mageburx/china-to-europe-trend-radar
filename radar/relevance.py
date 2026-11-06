"""Strict headline phrase filters for RSS. A query match is not a product mention.

Only exact product-oriented phrases are counted. We prefer false negatives over
spurious trend signals from generic articles on cars, pets or storage.
"""
from __future__ import annotations
import unicodedata

# Whitelist: specific product terminology per market, not loose topical words.
# China feed language is not proof that the publisher/consumer is Chinese.
ALIASES: dict[str,dict[str,tuple[str,...]]] = {
    "car_gap": {
        "CN":("座椅缝隙收纳", "汽车座椅夹缝收纳", "座椅缝隙收纳盒"),
        "PT":("organizador entre bancos", "organizador para bancos do carro"),
        "ES":("organizador hueco asiento", "organizador entre asientos"),
    },
    "cable_clips": {
        "CN":("理线夹", "理线器", "电线整理夹"),
        "PT":("organizador de cabos", "clips organizadores de cabos", "clips para cabos"),
        "ES":("organizador de cables", "clips para cables"),
    },
    "shoe_racks": {
        "CN":("模块化鞋架", "组合鞋架", "叠加鞋架", "可堆叠鞋架"),
        "PT":("sapateira modular", "organizador modular de sapatos"),
        "ES":("zapatero modular", "organizador modular de zapatos"),
    },
    "packing_cubes": {
        "CN":("旅行收纳包", "旅行压缩收纳袋", "行李压缩收纳", "旅行收纳袋"),
        "PT":("organizador de viagem compressivel", "sacos de compressao viagem", "packing cubes"),
        "ES":("organizadores de maleta compresion", "bolsas de compresion para viaje", "packing cubes"),
    },
    "pet_brush": {
        "CN":("宠物梳毛手套", "宠物刷毛手套", "梳毛手套"),
        "PT":("luva de escovagem para animais", "luva escova animais"),
        "ES":("guante cepillo mascotas", "guante de cepillado para mascotas"),
    },
    "underbed_storage": {
        "CN":("床底收纳箱", "床下收纳箱", "床底折叠收纳"),
        "PT":("arrumacao debaixo da cama", "caixa de arrumacao sob a cama"),
        "ES":("almacenamiento debajo de la cama", "caja de almacenaje bajo cama"),
    },
    "drawer_dividers": {
        "CN":("抽屉分隔板", "可调节抽屉隔板", "抽屉收纳隔板"),
        "PT":("divisorias ajustaveis gavetas", "separadores de gavetas"),
        "ES":("separadores ajustables cajones", "divisores para cajones"),
    },
    "car_trunk": {
        "CN":("后备箱收纳箱", "后备箱整理箱", "后备箱收纳盒"),
        "PT":("organizador de bagageira", "organizador dobravel bagageira"),
        "ES":("organizador de maletero", "organizador maletero plegable"),
    },
}


def normalize(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD",text.casefold())
                   if not unicodedata.combining(c))


def relevant_headline(product_id: str, market: str, title: str, source: str) -> bool:
    """A manually documented source is visible; news headlines require strict phrases."""
    if not source.startswith("Google News"):
        return True
    phrases=ALIASES.get(product_id,{}).get(market,())
    title_normal=normalize(title)
    return any(normalize(p) in title_normal for p in phrases)
