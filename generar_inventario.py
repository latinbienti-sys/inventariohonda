# -*- coding: utf-8 -*-
"""
Generador automatico de inventario Honda desde Odoo (latinbienmotors.com) - SOLO LECTURA.

Consulta el stock en vivo y regenera:
  - index.html  (pagina publicada en GitHub Pages)
  - Inventario_Carros_Honda.html
  - Inventario_Carros_Honda_Resumen.csv
  - Inventario_Carros_Honda_Detalle.csv

Credenciales via variables de entorno: ODOO_USER y ODOO_PASSWORD.
Este script NO contiene credenciales en texto plano.
"""
import csv
import io
import json
import os
import sys
import unicodedata
import urllib.request
import http.cookiejar
import time

BASE = "https://latinbienmotors.com"
DB = os.environ.get("ODOO_DB", "latinbien")
USER = os.environ.get("ODOO_USER", "")
PWD = os.environ.get("ODOO_PASSWORD", "")

if not USER or not PWD:
    sys.exit("ERROR: Faltan variables de entorno ODOO_USER / ODOO_PASSWORD")

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def rpc(url, method, params):
    payload = {"jsonrpc": "2.0", "method": "call", "params": params, "id": 1}
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    resp = opener.open(req, timeout=90)
    return json.loads(resp.read().decode())


def call_kw(model, method, args=None, kwargs=None):
    if args is None:
        args = []
    if kwargs is None:
        kwargs = {}
    res = rpc(BASE + "/web/dataset/call_kw", model, {
        "model": model, "method": method, "args": args, "kwargs": kwargs
    })
    if "error" in res:
        raise RuntimeError(json.dumps(res["error"], ensure_ascii=False)[:2000])
    return res["result"]


def strip_accents(s):
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def clean_color(name):
    """'Color: Azul Nordico' -> 'Azul Nordico' (sin acentos, consistente con la pagina)."""
    if name.startswith("Color:"):
        name = name[len("Color:"):].strip()
    return strip_accents(name).strip()


WH_MAP = {"LATIN": "LATIN", "CLBM": "CONSIGNACION", "RLBM": "RESERVAS", "RPLMH": "REPARACION"}


def warehouse_for(loc_name):
    prefix = loc_name.split("/")[0].strip().upper()
    return WH_MAP.get(prefix, prefix)


def get_data():
    # 1) Autenticar
    auth = rpc(BASE + "/web/session/authenticate", "call", {
        "db": DB, "login": USER, "password": PWD
    })
    r = auth.get("result", {})
    if not r.get("uid"):
        raise RuntimeError("Fallo de autenticacion en Odoo")

    # 2) Atributo "Color"
    attrs = call_kw("product.attribute", "search_read", [[]], {"fields": ["name"], "limit": 200})
    color_attr_id = None
    for a in attrs:
        if a["name"].strip().lower() == "color":
            color_attr_id = a["id"]
            break
    if not color_attr_id:
        raise RuntimeError("No se encontro el atributo Color")

    # 3) Templates de vehiculos con color
    templates = call_kw("product.template", "search_read",
                        [[["attribute_line_ids.attribute_id", "=", color_attr_id]]],
                        {"fields": ["display_name", "categ_id", "product_variant_ids"], "limit": 200})

    variant_ids = []
    for t in templates:
        variant_ids += t["product_variant_ids"]
    variant_ids = list(dict.fromkeys(variant_ids))

    # 4) Variantes -> color (ptav) y modelo
    variants = call_kw("product.product", "search_read",
                       [[["id", "in", variant_ids]]],
                       {"fields": ["product_template_attribute_value_ids"]})

    ptav_ids = []
    for v in variants:
        ptav_ids += v["product_template_attribute_value_ids"]
    ptav_ids = list(dict.fromkeys(ptav_ids))

    color_by_ptav = {}
    if ptav_ids:
        ptavs = call_kw("product.template.attribute.value", "search_read",
                        [[["id", "in", ptav_ids]]],
                        {"fields": ["product_attribute_value_id"]})
        for p in ptavs:
            if p.get("product_attribute_value_id"):
                color_by_ptav[p["id"]] = clean_color(p["product_attribute_value_id"][1])

    modelo_by_prod = {}
    cat_by_prod = {}
    color_by_prod = {}
    for t in templates:
        for vid in t["product_variant_ids"]:
            modelo_by_prod[vid] = t["display_name"]
            cat_by_prod[vid] = t["categ_id"][1] if t.get("categ_id") else ""
    for v in variants:
        pids = v["product_template_attribute_value_ids"]
        for p in pids:
            if p in color_by_ptav:
                color_by_prod[v["id"]] = color_by_ptav[p]
                break

    # 5) Quants
    quants = call_kw("stock.quant", "search_read",
                     [[["product_id", "in", variant_ids]]],
                     {"fields": ["product_id", "location_id", "quantity", "reserved_quantity", "lot_id"]})

    # 6) Ubicaciones
    loc_ids = list({q["location_id"][0] for q in quants})
    locs = call_kw("stock.location", "search_read",
                   [[["id", "in", loc_ids]]],
                   {"fields": ["complete_name", "usage"]})
    loc_usage = {l["id"]: l["usage"] for l in locs}
    loc_name = {l["id"]: l["complete_name"] for l in locs}

    # 7) Lotes (VIN)
    lot_ids = list({q["lot_id"][0] for q in quants if q.get("lot_id")})
    lots = call_kw("stock.lot", "search_read",
                   [[["id", "in", lot_ids]]],
                   {"fields": ["name"]})
    lot_name = {l["id"]: (l["name"] or "").strip() for l in lots}

    # 8) Consolidar solo ubicaciones internas con cantidad > 0
    agg = {}
    for q in quants:
        loc_id = q["location_id"][0]
        if loc_usage.get(loc_id) != "internal":
            continue
        qty = float(q["quantity"] or 0.0)
        if qty <= 0:
            continue
        prod_id = q["product_id"][0]
        lot_id = q["lot_id"][0] if q.get("lot_id") else None
        key = (prod_id, lot_id)
        e = agg.setdefault(key, {
            "prod": prod_id, "lot": lot_id, "qty": 0.0, "res": 0.0, "loc": loc_id
        })
        e["qty"] += qty
        e["res"] += float(q["reserved_quantity"] or 0.0)
        # ubicacion con mayor cantidad define el almacen
        if qty > 0:
            e["loc"] = loc_id

    units = []
    for (prod_id, lot_id), e in agg.items():
        vin = lot_name.get(lot_id, "") if lot_id else ""
        if not vin:
            continue
        units.append({
            "modelo": modelo_by_prod.get(prod_id, ""),
            "cat": cat_by_prod.get(prod_id, ""),
            "color": color_by_prod.get(prod_id, ""),
            "alma": warehouse_for(loc_name.get(e["loc"], "")),
            "qty": int(e["qty"]),
            "res": 1 if e["res"] > 0 else 0,
            "vin": vin,
        })

    # Orden estable: almacen, modelo, color, vin
    units.sort(key=lambda u: (u["alma"], u["modelo"], u["color"], u["vin"]))
    return units


def build_html(units):
    total = len(units)
    por_alma = {}
    for u in units:
        por_alma[u["alma"]] = por_alma.get(u["alma"], 0) + 1

    unidades_js = json.dumps(
        [{"modelo": u["modelo"], "cat": u["cat"], "color": u["color"],
          "alma": u["alma"], "res": bool(u["res"]), "vin": u["vin"]} for u in units],
        ensure_ascii=False, separators=(",", ":")
    )

    updated = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())

    html = HTML_TEMPLATE
    html = html.replace("__KPI_TOTAL__", str(total))
    html = html.replace("__KPI_CONS__", str(por_alma.get("CONSIGNACION", 0)))
    html = html.replace("__KPI_RES__", str(por_alma.get("RESERVAS", 0)))
    html = html.replace("__KPI_REP__", str(por_alma.get("REPARACION", 0)))
    html = html.replace("__UNIDADES__", unidades_js)
    html = html.replace("__UPDATED__", updated)
    return html


def write_csv(units):
    # Resumen: Almacen;Categoria;Modelo;Color;Unidades;Reservadas
    groups = {}
    for u in units:
        k = (u["alma"], u["cat"], u["modelo"], u["color"])
        groups.setdefault(k, [0, 0])
        groups[k][0] += u["qty"]
        groups[k][1] += u["res"]
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(["Almacen", "Categoria", "Modelo", "Color", "Unidades", "Reservadas"])
    for k in sorted(groups):
        w.writerow([k[0], k[1], k[2], k[3], groups[k][0], groups[k][1]])
    with open("Inventario_Carros_Honda_Resumen.csv", "w", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())

    # Detalle: Modelo;Categoria;Color;Almacen;Cantidad;Reservada;Vin
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(["Modelo", "Categoria", "Color", "Almacen", "Cantidad", "Reservada", "Vin"])
    for u in units:
        w.writerow([u["modelo"], u["cat"], u["color"], u["alma"], u["qty"], u["res"], u["vin"]])
    with open("Inventario_Carros_Honda_Detalle.csv", "w", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Inventario Vehículos Honda — LatinBienMotors</title>
<style>
  :root{
    --honda-red: #cc0000;
    --honda-blue: #16213e;
    --bg: #f4f6fb;
    --card: #ffffff;
    --text: #1c2333;
    --muted: #5c6b8a;
    --accent: #0f3460;
    --border: #e3e8f2;
  }
  *{ box-sizing:border-box; margin:0; padding:0; }
  body{ font-family:'Segoe UI', Arial, sans-serif; background:var(--bg); color:var(--text); padding:24px; }
  .wrap{ max-width:1200px; margin:0 auto; }

  header{
    background:linear-gradient(135deg, var(--honda-blue), var(--accent));
    color:#fff; border-radius:14px; padding:22px 26px;
    display:flex; align-items:center; justify-content:space-between;
    flex-wrap:wrap; gap:16px; box-shadow:0 8px 22px rgba(0,0,0,.12);
  }
  header .logo{ font-size:26px; font-weight:800; letter-spacing:.5px; }
  header .logo span{ color:#ffd24a; }
  header .meta{ font-size:13px; opacity:.9; text-align:right; }

  .kpis{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:14px; margin:22px 0; }
  .kpi{ background:var(--card); border-radius:12px; padding:18px; box-shadow:0 3px 10px rgba(0,0,0,.05); border-top:4px solid var(--accent); }
  .kpi .v{ font-size:32px; font-weight:800; color:var(--accent); }
  .kpi .l{ font-size:13px; color:var(--muted); margin-top:4px; text-transform:uppercase; letter-spacing:.4px; }
  .kpi.green{ border-top-color:#1e9e5a;} .kpi.green .v{ color:#1e9e5a;}
  .kpi.yellow{ border-top-color:#f59e0b;} .kpi.yellow .v{ color:#b45309;}
  .kpi.red{ border-top-color:var(--honda-red);} .kpi.red .v{ color:var(--honda-red);}

  .grid{ display:grid; gap:16px; margin-bottom:16px; }
  .grid.cols-2{ grid-template-columns:1fr 1fr; }
  @media(max-width:900px){ .grid.cols-2{ grid-template-columns:1fr; } }

  .card{ background:var(--card); border-radius:12px; padding:18px; box-shadow:0 3px 10px rgba(0,0,0,.05); }
  .card h3{ font-size:15px; color:var(--accent); margin-bottom:14px; display:flex; align-items:center; gap:8px; }
  .card h3::before{ content:''; width:6px; height:18px; background:#cc0000; border-radius:3px; }

  /* ---------- BARRAS SIMPLES ---------- */
  .bars{ width:100%; height:240px; display:flex; align-items:flex-end; gap:10px; }
  .bar-col{ flex:1; display:flex; flex-direction:column; align-items:center; justify-content:flex-end; height:100%; min-width:0; }
  .bar-col .bar{
    width:100%; max-width:70px; border-radius:8px 8px 0 0;
    background:linear-gradient(180deg, #cc0000, #8c0000);
    position:relative; transition:height .6s ease;
  }
  .bar-col .bar .cnt{ position:absolute; top:-26px; left:0; right:0; text-align:center; font-weight:800; color:#222; font-size:16px; }
  .bar-col .lab{ margin-top:8px; font-size:11px; color:var(--muted); text-align:center; word-break:break-word; }

  /* ---------- TARJETAS DE COLORES POR MODELO ---------- */
  .model-grid{ display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:14px; }
  .model-card{
    background:linear-gradient(160deg,#f9fbff,#ffffff);
    border:1px solid var(--border); border-radius:14px; padding:16px;
    box-shadow:0 4px 14px rgba(0,0,0,.06); transition:transform .2s, box-shadow .2s;
  }
  .model-card:hover{ transform:translateY(-3px); box-shadow:0 10px 22px rgba(0,0,0,.10); }
  .mc-head{ display:flex; align-items:center; justify-content:space-between; margin-bottom:12px; }
  .mc-name{ font-weight:800; font-size:15px; color:var(--honda-blue); letter-spacing:.2px; }
  .mc-total{ background:var(--honda-blue); color:#fff; border-radius:20px; padding:3px 13px; font-weight:800; font-size:15px; }
  .mc-bar{ display:flex; height:14px; border-radius:8px; overflow:hidden; margin-bottom:12px; background:#eef1f8; }
  .mc-seg{ height:100%; transition:width .7s ease; }
  .mc-chips{ display:flex; flex-wrap:wrap; gap:6px; }
  .chip{
    display:inline-flex; align-items:center; gap:6px;
    background:#fff; border:1px solid var(--border); border-radius:20px;
    padding:4px 10px; font-size:12px; color:var(--text);
  }
  .chip .sw{ width:12px; height:12px; border-radius:50%; border:1px solid rgba(0,0,0,.12); }
  .chip b{ color:var(--accent); margin-left:2px; }

  /* ---------- Tabla ---------- */
  table{ width:100%; border-collapse:collapse; font-size:13px; }
  th,td{ padding:9px 10px; text-align:left; border-bottom:1px solid var(--border); }
  th{ background:var(--honda-blue); color:#fff; font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.4px; }
  tr:nth-child(even){ background:#f8faff; }
  .badge{ display:inline-block; padding:2px 10px; border-radius:20px; font-size:11px; font-weight:700; color:#fff; }
  .b-cons{ background:#1e9e5a; } .b-res{ background:#f59e0b; } .b-rep{ background:#e11d48; } .b-lat{ background:#536dfe; }
  footer{ text-align:center; margin-top:18px; font-size:12px; color:var(--muted); }
</style>
</head>
<body>
<div class="wrap">

  <header>
    <div>
      <div class="logo">Inventario Vehículos <span>HONDA</span></div>
      <div style="font-size:13px;opacity:.85;margin-top:4px;">Honda Mérida — LatinBienMotors, C.A.</div>
    </div>
  </header>

  <!-- KPIs -->
  <div class="kpis">
    <div class="kpi"><div class="v">__KPI_TOTAL__</div><div class="l">Total unidades en inventario</div></div>
    <div class="kpi green"><div class="v">__KPI_CONS__</div><div class="l">Almacén Consignación</div></div>
    <div class="kpi yellow"><div class="v">__KPI_RES__</div><div class="l">Almacén Reservas</div></div>
    <div class="kpi red"><div class="v">__KPI_REP__</div><div class="l">Almacén Reparación</div></div>
  </div>

  <!-- Gráfico principal: Colores por modelo -->
  <div class="card" style="margin-bottom:16px;">
    <h3>Colores por Modelo</h3>
    <div class="model-grid" id="modelosGrid"></div>
  </div>

  <!-- Gráficos secundarios -->
  <div class="grid cols-2">
    <div class="card">
      <h3>Unidades por Almacén</h3>
      <div class="bars" id="barsAlmacen"></div>
    </div>
    <div class="card">
      <h3>Resumen por Modelo</h3>
      <table>
        <thead><tr><th>Modelo</th><th>Unidades</th></tr></thead>
        <tbody id="tblResumen"></tbody>
      </table>
    </div>
  </div>

  <!-- Tabla detalle completa -->
  <div class="card">
    <h3>Detalle del inventario (por unidad — VIN)</h3>
    <table>
      <thead>
        <tr>
          <th>#</th><th>Modelo</th><th>Color</th>
          <th>Almacén</th><th>VIN</th>
        </tr>
      </thead>
      <tbody id="tblDet"></tbody>
    </table>
  </div>

  <footer>LatinBienMotors — Honda Mérida | Inventario de Vehículos<br><span style="opacity:.7">Actualizado: __UPDATED__</span></footer>
</div>

<script>
"use strict";

// ================== DATOS ==================
const unidades = __UNIDADES__;

const group = arr => arr.reduce((m, v) => (m[v] = (m[v] || 0) + 1, m), {});

const COLOR_HEX = {
  "Azul Cosmico":   "#0ea5e9",
  "Azul Nordico":   "#4f46e5",
  "Blanco":         "#fafafa",
  "Blanco Perlado": "#e5e7eb",
  "Gris Metalico":  "#9ca3af",
  "Plata Metalico": "#d1d5db",
  "Rojo Metalico":  "#cc0000"
};

// ---------- Colores por modelo (tarjetas) ----------
function shortModel(m){
  return m.replace("HONDA ", "").replace(" 2026", "").replace("1.5L A/T ", "").trim();
}

const modelos = {};
unidades.forEach(u => {
  const sm = shortModel(u.modelo);
  if (!modelos[sm]) modelos[sm] = {};
  modelos[sm][u.color] = (modelos[sm][u.color] || 0) + 1;
});

const gridEl = document.getElementById("modelosGrid");
gridEl.innerHTML = Object.keys(modelos).map(m => {
  const colores = Object.entries(modelos[m]).sort((a,b) => b[1]-a[1]);
  const total = colores.reduce((s,c)=>s+c[1],0);
  const segs = colores.map(([c, n]) =>
    `<div class="mc-seg" style="width:${(n/total*100).toFixed(1)}%;background:${COLOR_HEX[c]||'#94a3b8'}" title="${c}: ${n}"></div>`
  ).join("");
  const chips = colores.map(([c, n]) =>
    `<span class="chip"><span class="sw" style="background:${COLOR_HEX[c]||'#94a3b8'}"></span>${c} <b>${n}</b></span>`
  ).join("");
  return `<div class="model-card">
            <div class="mc-head"><span class="mc-name">${m}</span><span class="mc-total">${total}</span></div>
            <div class="mc-bar">${segs}</div>
            <div class="mc-chips">${chips}</div>
          </div>`;
}).join("");

// ---------- Barras: unidades por almacén ----------
const ALMACEN_COLOR = { "CONSIGNACION":"#1e9e5a", "RESERVAS":"#f59e0b", "REPARACION":"#e11d48", "LATIN":"#536dfe" };
function buildBars(id, data, colorMap){
  const max = Math.max(...Object.values(data), 1);
  const el = document.getElementById(id);
  el.innerHTML = "";
  Object.entries(data).forEach(([k, v]) => {
    const col = document.createElement("div");
    col.className = "bar-col";
    const bar = document.createElement("div");
    bar.className = "bar";
    bar.style.height = (v / max * 100).toFixed(1) + "%";
    bar.style.background = colorMap[k] || "#cc0000";
    const cnt = document.createElement("span");
    cnt.className = "cnt"; cnt.textContent = v;
    bar.appendChild(cnt);
    const lab = document.createElement("span");
    lab.className = "lab"; lab.textContent = k;
    col.appendChild(bar); col.appendChild(lab);
    el.appendChild(col);
  });
}
const porAlma = group(unidades.map(u => u.alma));
buildBars("barsAlmacen", porAlma, ALMACEN_COLOR);

// ---------- Tabla resumen por modelo ----------
const porModelo = group(unidades.map(u => shortModel(u.modelo)));
const tblResumen = document.getElementById("tblResumen");
tblResumen.innerHTML = Object.entries(porModelo).map(([m, n]) =>
  `<tr><td>${m}</td><td><b>${n}</b></td></tr>`
).join("") + `<tr style="background:#eef2fb;font-weight:700"><td>TOTAL</td><td>${unidades.length}</td></tr>`;

// ---------- Tabla detalle ----------
const bdg = a => a === "CONSIGNACION" ? "b-cons" : a === "RESERVAS" ? "b-res" : a === "REPARACION" ? "b-rep" : "b-lat";
const tblDet = document.getElementById("tblDet");
tblDet.innerHTML = unidades.map((u, i) => `
  <tr>
    <td>${i + 1}</td>
    <td>${u.modelo}</td>
    <td><span class="sw" style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${COLOR_HEX[u.color]||'#94a3b8'};border:1px solid rgba(0,0,0,.15);vertical-align:middle;margin-right:6px"></span>${u.color}</td>
    <td><span class="badge ${bdg(u.alma)}">${u.alma}</span></td>
    <td style="font-family:Consolas,monospace">${u.vin}</td>
  </tr>`).join("");
</script>
</body>
</html>
"""


def main():
    print("Conectando a Odoo...")
    units = get_data()
    print(f"Unidades encontradas: {len(units)}")
    for u in units:
        print(f"  {u['alma']:12s} | {u['modelo']:34s} | {u['color']:15s} | res={u['res']} | {u['vin']}")

    html = build_html(units)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    with open("Inventario_Carros_Honda.html", "w", encoding="utf-8") as f:
        f.write(html)

    write_csv(units)
    print("Archivos generados: index.html, Inventario_Carros_Honda.html, *_Resumen.csv, *_Detalle.csv")


if __name__ == "__main__":
    main()
