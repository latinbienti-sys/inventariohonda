# -*- coding: utf-8 -*-
"""Sondeo SOLO LECTURA: lineas de la orden de ROMINA BARBACANE."""
import json, os, urllib.request, http.cookiejar

BASE = "https://latinbienmotors.com"
DB = "latinbien"
USER = os.environ.get("ODOO_USER", "")
PWD = os.environ.get("ODOO_PASSWORD", "")

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def rpc(url, method, params):
    payload = {"jsonrpc": "2.0", "method": "call", "params": params, "id": 1}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    return json.loads(opener.open(req, timeout=90).read().decode())

def call_kw(model, method, args=None, kwargs=None):
    if args is None: args = []
    if kwargs is None: kwargs = {}
    res = rpc(BASE + "/web/dataset/call_kw", model, {"model": model, "method": method, "args": args, "kwargs": kwargs})
    if "error" in res:
        raise RuntimeError(json.dumps(res["error"], ensure_ascii=False)[:2000])
    return res["result"]

rpc(BASE + "/web/session/authenticate", "call", {"db": DB, "login": USER, "password": PWD})

# Atributo Color para identificar modelos de vehiculos
attrs = call_kw("product.attribute", "search_read", [[]], {"fields": ["name"], "limit": 200})
cid = [a["id"] for a in attrs if a["name"].strip().lower() == "color"][0]
tpls = call_kw("product.template", "search_read",
               [[["attribute_line_ids.attribute_id", "=", cid]]],
               {"fields": ["display_name", "product_variant_ids"], "limit": 200})
variant_ids = [vid for t in tpls for vid in t["product_variant_ids"]]

lines = call_kw("sale.order.line", "search_read",
                [[["order_id", "=", 4425]]],
                {"fields": ["order_id", "product_id", "product_uom_qty", "qty_delivered", "price_unit", "price_subtotal", "state", "name"], "limit": 100})
print("Lineas de la orden 4425 (ROMINA BARBACANE):", len(lines))
for l in lines:
    pid = l.get("product_id")
    if pid and pid[0] in variant_ids:
        tipo = "VEHICULO"
    else:
        tipo = "otro"
    print("  ", tipo, "|", (pid[1] if pid else "-"), "| qty:", l.get("product_uom_qty"),
          "| entregado:", l.get("qty_delivered"), "| price:", l.get("price_unit"),
          "| subtotal:", l.get("price_subtotal"), "| state:", l.get("state"))