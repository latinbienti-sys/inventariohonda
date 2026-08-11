# -*- coding: utf-8 -*-
"""Sondeo SOLO LECTURA: presupuestos draft, buscar 'romina'."""
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

# Partners con 'romina'
parts = call_kw("res.partner", "search_read", [[["name", "ilike", "romina"]]], {"fields": ["id", "name"], "limit": 20})
print("Partners 'romina':")
for p in parts:
    print("  ", p["id"], p["name"])

# Todas las ordenes draft
orders = call_kw("sale.order", "search_read", [[["state", "=", "draft"]]],
                 {"fields": ["name", "partner_id", "date_order", "amount_total"], "limit": 300})
print("\nTodas las ordenes draft (%d):" % len(orders))
for o in orders:
    print("  ", o["id"], "|", o["name"], "|", (o["partner_id"][1] if o.get("partner_id") else "?"), "|", o["date_order"])