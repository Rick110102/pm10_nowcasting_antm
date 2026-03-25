import requests
import folium
import json
import os
import time
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ══════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════
CLIENT_ID     = "antamina"
CLIENT_SECRET = os.environ.get("METEOSIM_SECRET", "")
TOKEN_URL     = "https://sso.meteosim.com/realms/suite/protocol/openid-connect/token"
API_BASE      = "https://api.meteosim.com"
SITE_PRON     = "antamina_predictions"
SITE_OBS      = "antamina_observations"
TOPIC         = "nowcasting"
PERU_TZ       = timezone(timedelta(hours=-5))

ESTACIONES = [
    {"nombre": "Usupallares", "location_code": "USUPALLARES","lat": -9.55422, "lng": -77.07305, "buffer_m": 1000},
    {"nombre": "Dos Cruces",  "location_code": "2CRUCES",    "lat": -9.56023, "lng": -77.05986, "buffer_m": 1000},
    {"nombre": "Quebrada",    "location_code": "QUEBRADA",   "lat": -9.55501, "lng": -77.08584, "buffer_m": 1000},
    {"nombre": "Tucush",      "location_code": "TUCUSH",     "lat": -9.51011, "lng": -77.05715, "buffer_m": 1000},
]

# ══════════════════════════════════════════════════════════
# TOKEN
# ══════════════════════════════════════════════════════════
def get_token():
    r = requests.post(TOKEN_URL, data={
        "grant_type":    "client_credentials",
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET
    })
    r.raise_for_status()
    print("[Token] Obtenido ✓")
    return r.json()["access_token"]

# ══════════════════════════════════════════════════════════
# RECORD CODES — timestamp fijo como parámetro
# ══════════════════════════════════════════════════════════
def get_record_code_pron(location_code, ts):
    code = (f"alertdata:nowcasting:antamina_predictions:"
            f"antamina-nowcasting:AlertaPM10_5minutal:{location_code}:{ts}")
    print(f"  [PRON] {code}")
    return code

def get_record_code_obs(location_code, ts):
    ts_obs = ts - 7200
    code = (f"alertdata:nowcasting:antamina_observations:"
            f"antamina-nowcasting:AlertaPM10_5minutal:{location_code}:{ts_obs}")
    print(f"  [OBS]  {code}")
    return code

# ══════════════════════════════════════════════════════════
# CONSULTAR SERIE TEMPORAL
# ══════════════════════════════════════════════════════════
def get_timeserie(token, record_code, site_id):
    url = f"{API_BASE}/v3/alertdata/{site_id}/topics/{TOPIC}/records/{record_code}/timeserie"
    r = requests.get(url, headers={
        "Accept":        "application/json",
        "Authorization": f"Bearer {token}"
    })
    print(f"  [{site_id}] Status: {r.status_code}")
    r.raise_for_status()
    return r.json()["items"]

# ══════════════════════════════════════════════════════════
# PROCESAR PRONÓSTICO
# ══════════════════════════════════════════════════════════
def procesar_pron(items):
    pronostico = []
    for item in items:
        t = datetime.fromisoformat(item["time"].replace("Z", "+00:00"))
        t = t.astimezone(PERU_TZ).replace(tzinfo=None)
        val = next((v["value"] for v in item.get("values", [])
                    if v["variableId"] == "PM10"), None)
        if val is None:
            continue
        pronostico.append({"time": t, "value": round(val, 4)})

    if not pronostico:
        return []

    pronostico.sort(key=lambda x: x["time"])
    inicio = pronostico[0]["time"]
    fin    = inicio + timedelta(hours=1, minutes=55)
    return [r for r in pronostico if r["time"] <= fin]

# ══════════════════════════════════════════════════════════
# PROCESAR OBSERVADOS
# ══════════════════════════════════════════════════════════
def procesar_obs(items):
    observados = []
    for item in items:
        t = datetime.fromisoformat(item["time"].replace("Z", "+00:00"))
        t = t.astimezone(PERU_TZ).replace(tzinfo=None)
        val = next((v["value"] for v in item.get("values", [])
                    if v["variableId"] == "PM10"), None)
        if val is None:
            continue
        observados.append({"time": t, "value": round(val, 4)})

    observados.sort(key=lambda x: x["time"])
    return observados

# ══════════════════════════════════════════════════════════
# COLOR
# ══════════════════════════════════════════════════════════
def get_color(val):
    if val > 100: return "#ef4444", "MUY ALTO", "🔴"
    return             "#22c55e",  "BAJO",     "🟢"

# ══════════════════════════════════════════════════════════
# MAPA FOLIUM
# ══════════════════════════════════════════════════════════
def generar_mapa(resultados):
    lat_c = sum(e["lat"] for e in ESTACIONES) / len(ESTACIONES)
    lng_c = sum(e["lng"] for e in ESTACIONES) / len(ESTACIONES)
    m = folium.Map(
        location=[lat_c, lng_c],
        zoom_start=13,
        tiles="https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Tiles © Esri — Source: Esri, Maxar, Earthstar Geographics",
        zoom_control=True
    )

    for est in resultados:
        color, categoria, emoji = get_color(est["max_val"])
        km = est["buffer_m"] / 1000

        folium.Circle(
            location=[est["lat"], est["lng"]],
            radius=est["buffer_m"],
            color=color, fill=True, fill_color=color,
            fill_opacity=0.22, weight=2,
            tooltip=f"{est['nombre']} · {est['max_val']:.2f} μg/m³ · {emoji} {categoria}"
        ).add_to(m)

        folium.CircleMarker(
            location=[est["lat"], est["lng"]],
            radius=6,
            color="#ffffff", fill=True, fill_color=color,
            fill_opacity=1, weight=2,
            popup=folium.Popup(f"""
                <div style='font-family:monospace;min-width:190px;padding:4px'>
                <b>📍 {est['nombre']}</b>
                <hr style='margin:4px 0'>
                PM10 Máx: <b>{est['max_val']:.2f} μg/m³</b><br>
                Categoría: <b style='color:{color}'>{emoji} {categoria}</b><br>
                Hora máx: <b>{est['max_time'].strftime('%H:%M') if est['max_time'] else '—'}</b><br>
                Buffer: <b>{km:.0f} km</b>
                </div>""", max_width=220),
            tooltip=f"{est['nombre']} · {est['max_val']:.2f} μg/m³"
        ).add_to(m)

        folium.Marker(
            location=[est["lat"], est["lng"]],
            icon=folium.DivIcon(
                html=f"""<div style="
                    font-family:'Share Tech Mono',monospace;
                    font-size:10px;
                    color:#ffffff;
                    white-space:nowrap;
                    pointer-events:none;
                    transform:translate(-80%, 80%);
                ">{est['nombre']}</div>""",
            )
        ).add_to(m)

    return m.get_root().render()

# ══════════════════════════════════════════════════════════
# CHART DATA
# ══════════════════════════════════════════════════════════
def preparar_chart_data(resultados):
    charts = []
    for est in resultados:
        color, _, _ = get_color(est["max_val"])
        pron = est["pronostico"]
        obs  = est["observados"]

        if pron:
            inicio    = pron[0]["time"]
            ventana_i = inicio - timedelta(hours=2)
            ventana_f = inicio + timedelta(hours=1, minutes=55)
            eje_x = []
            t = ventana_i
            while t <= ventana_f:
                eje_x.append(t.strftime("%H:%M"))
                t += timedelta(minutes=5)
        else:
            eje_x = []

        obs_data  = [{"x": r["time"].strftime("%H:%M"), "y": r["value"]} for r in obs]
        pron_data = [{"x": r["time"].strftime("%H:%M"), "y": r["value"]} for r in pron]
        max_item  = max(pron, key=lambda x: x["value"]) if pron else None

        charts.append({
            "nombre":      est["nombre"],
            "color":       color,
            "obs":         obs_data,
            "pron":        pron_data,
            "max_val":     est["max_val"],
            "max_time":    max_item["time"].strftime("%H:%M") if max_item else None,
            "eje_x":       eje_x,
            "inicio_pron": pron[0]["time"].strftime("%H:%M") if pron else None,
        })
    return json.dumps(charts, ensure_ascii=False)

# ══════════════════════════════════════════════════════════
# HTML FINAL
# ══════════════════════════════════════════════════════════
def generar_html(resultados, mapa_render, ahora):
    chart_data = preparar_chart_data(resultados)
    fecha_act  = ahora.strftime("%d/%m/%Y %H:%M")
    hora_corte = ahora.strftime("%H:%M")
    hora_prox  = (ahora + timedelta(minutes=5)).strftime("%H:%M")

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <meta http-equiv="refresh" content="300"/>
  <title>Monitor Nowcasting PM10 · Antamina</title>
  <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
  <link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Barlow:wght@300;400;600&display=swap" rel="stylesheet"/>
  <style>
    :root {{--bg:#E3E3E3;--panel:#F5F5F5;--border:#1e2d3d;--accent:#000000;--text:#c9d8e8;--muted:#4a6070;--legend:#F5F5F5;}}
    *{{margin:0;padding:0;box-sizing:border-box;}}
    html,body{{height:100%;background:var(--bg);color:var(--text);font-family:'Barlow',sans-serif;overflow:hidden;}}
    header{{height:48px;flex-shrink:0;background:var(--panel);border-bottom:1px solid var(--border);padding:0 20px;display:flex;align-items:center;gap:16px;}}
    .logo{{font-family:'Share Tech Mono',monospace;font-size:20px;color:var(--accent);letter-spacing:2px;}}
    .hdr-right{{margin-left:auto;font-family:'Share Tech Mono',monospace;font-size:11px;color:var(--muted);display:flex;gap:20px;}}
    .hdr-right span{{color:var(--accent);}}
    .body{{display:grid;grid-template-columns:40% 60%;grid-template-rows:calc(100vh - 84px) 36px;height:calc(100vh - 48px);}}
    .charts-panel{{grid-column:1;grid-row:1;display:flex;flex-direction:column;overflow:hidden;border-right:1px solid var(--border);background:var(--bg);}}
    .charts-scroll{{flex:1;overflow-y:auto;padding:8px 10px;display:flex;flex-direction:column;gap:6px;}}
    .chart-block{{flex-shrink:0;}}
    .chart-label{{font-family:'Share Tech Mono',monospace;font-size:12px;color:var(--accent);letter-spacing:1px;margin-bottom:2px;padding-left:2px;}}
    .plotly-div{{width:100%;height:180px;}}
    .map-panel{{grid-column:2;grid-row:1;overflow:hidden;position:relative;}}
    .map-panel .folium-map{{width:100% !important;height:100% !important;}}
    .legend-charts{{grid-column:1;grid-row:2;background:var(--legend);border-top:1px solid var(--border);border-right:1px solid var(--border);display:flex;align-items:center;justify-content:center;padding:0 10px;gap:16px;font-family:'Share Tech Mono',monospace;font-size:9px;color:var(--muted);}}
    .li{{display:flex;align-items:center;gap:5px;}}
    .line-solid{{width:20px;height:2px;background:#283552;}}
    .line-pron{{width:20px;height:2px;background:#0070A3;}}
    .line-corte{{width:2px;height:12px;background:#E88D00;}}
    .dot-max{{width:8px;height:8px;border-radius:50%;background:#850B0B;}}
    .legend-map{{grid-column:2;grid-row:2;background:var(--legend);border-top:1px solid var(--border);display:flex;align-items:center;justify-content:center;padding:0 14px;gap:16px;font-family:'Share Tech Mono',monospace;font-size:9px;color:var(--muted);}}
    .dot{{width:10px;height:10px;border-radius:50%;flex-shrink:0;}}
    ::-webkit-scrollbar{{width:3px;}}
    ::-webkit-scrollbar-thumb{{background:var(--border);border-radius:2px;}}
  </style>
</head>
<body>
<header>
  <div class="logo">ANTAMINA - MONITOR NOWCASTING PM10</div>
  <div class="hdr-right">
    <div>Corte: <span>{hora_corte}</span></div>
    <div>Actualizado: <span>{fecha_act}</span> (Perú)</div>
    <div>Próx. <span>{hora_prox}</span></div>
  </div>
</header>
<div class="body">
  <div class="charts-panel"><div class="charts-scroll" id="charts-scroll"></div></div>
  <div class="map-panel" id="map-panel">{mapa_render}</div>
  <div class="legend-charts">
    <div class="li"><div class="line-solid"></div><span>Observado</span></div>
    <div class="li"><div class="line-pron"></div><span>Pronóstico</span></div>
    <div class="li"><div class="line-corte"></div><span>Inicio pronóstico</span></div>
    <div class="li"><div class="dot-max"></div><span>Máx. pronóstico</span></div>
  </div>
  <div class="legend-map">
    <div class="li"><div class="dot" style="background:#22c55e"></div><span>&lt; 100 μg/m³ Bajo</span></div>
    <div class="li"><div class="dot" style="background:#ef4444"></div><span>&gt; 100 μg/m³ Muy Alto</span></div>
    <div class="li">Buffer: Dos Cruces / Tucush / Usupallares / Quebrada = 1km</div>
  </div>
</div>
<script>
const CHART_DATA = {chart_data};
const HORA_CORTE = "{hora_corte}";
const EJE_X_FIJO = CHART_DATA[0].eje_x;

const LAYOUT_BASE = {{
  paper_bgcolor: '#E3E3E3',
  plot_bgcolor:  '#E3E3E3',
  font:   {{ family:'Share Tech Mono, monospace', size:9, color:'#c9d8e8' }},
  margin: {{ t:8, r:8, b:38, l:40 }},
  xaxis: {{
    showgrid:true, gridcolor:'#BBBBBB', gridwidth:1,
    tickfont:{{ size:9 }}, color:'#333333', tickangle:-45,
    type:'category',
    categoryorder:'array',
    categoryarray: EJE_X_FIJO,
    range: [-0.5, EJE_X_FIJO.length - 0.5],
  }},
  yaxis: {{
    showgrid:true, gridcolor:'#BBBBBB', gridwidth:1,
    tickfont:{{ size:9 }}, color:'#333333', rangemode:'tozero',
    title:{{ text:'μg/m³', font:{{ size:9, color:'#292929' }} }}
  }},
  showlegend: false,
}};

const scroll = document.getElementById('charts-scroll');

CHART_DATA.forEach((est, i) => {{
  const block = document.createElement('div');
  block.className = 'chart-block';
  const label = document.createElement('div');
  label.className = 'chart-label';
  label.textContent = '📍 ' + est.nombre;
  const div = document.createElement('div');
  div.className = 'plotly-div';
  div.id = 'ch' + i;
  block.appendChild(label);
  block.appendChild(div);
  scroll.appendChild(block);

  const traces = [];

  if (est.obs.length) {{
    traces.push({{
      x: est.obs.map(d => d.x),
      y: est.obs.map(d => d.y),
      type:'scatter', mode:'lines+markers',
      line:{{ color:'#283552', width:1.5 }},
      marker:{{color:'#283552', size:4, symbol:'circle'}},
      hovertemplate:'%{{x}}<br>%{{y:.2f}} μg/m³<extra></extra>',
    }});
  }}

  if (est.pron.length) {{
    traces.push({{
      x: est.pron.map(d => d.x),
      y: est.pron.map(d => d.y),
      type:'scatter', mode:'lines+markers',
      line:{{ color:'#0070A3', width:1.5 }},
      marker:{{color:'#0070A3', size:4, symbol:'circle'}},
      hovertemplate:'%{{x}}<br>%{{y:.2f}} μg/m³<extra></extra>',
    }});
  }}

  if (est.max_time) {{
    traces.push({{
      x:[est.max_time], y:[est.max_val],
      type:'scatter', mode:'markers+text',
      marker:{{ color:'#850B0B', size:8, symbol:'circle',
                line:{{ color:'#fff', width:1 }} }},
      text:[est.max_val.toFixed(2)],
      textposition:'bottom center',
      textfont:{{ color:'#850B0B', size:12 }},
      hovertemplate:'Máx: %{{y:.2f}} μg/m³<extra></extra>',
    }});
  }}

  const layout = JSON.parse(JSON.stringify(LAYOUT_BASE));
  layout.xaxis.categoryarray = est.eje_x;
  layout.xaxis.range = [-0.5, est.eje_x.length - 0.5];
  layout.shapes = [{{
    type:'line',
    x0: est.inicio_pron, x1: est.inicio_pron,
    y0:0, y1:1, yref:'paper',
    line:{{ color:'#E88D00', width:1.8, dash:'dash' }}
  }}];
  layout.annotations = [];

  Plotly.newPlot('ch'+i, traces, layout, {{
    responsive:true, displayModeBar:false
  }});
}});

setTimeout(function() {{
  if (window.L) {{
    Object.keys(window).forEach(function(k) {{
      if (k.startsWith('map_') && window[k] && window[k].invalidateSize) {{
        try {{ window[k].invalidateSize(); }} catch(e) {{}}
      }}
    }});
  }}
}}, 500);
</script>
</body>
</html>"""

# ══════════════════════════════════════════════════════════
# FUNCIÓN POR ESTACIÓN — para paralelizar
# ══════════════════════════════════════════════════════════
def consultar_estacion(est, token, ts_fijo):
    try:
        print(f"\n[{est['nombre']}] Consultando...")

        rc_pron    = get_record_code_pron(est["location_code"], ts_fijo)
        items_pron = get_timeserie(token, rc_pron, SITE_PRON)
        pron       = procesar_pron(items_pron)

        rc_obs    = get_record_code_obs(est["location_code"], ts_fijo)
        items_obs = get_timeserie(token, rc_obs, SITE_OBS)
        obs       = procesar_obs(items_obs)

        max_item  = max(pron, key=lambda x: x["value"]) if pron else None
        max_val   = max_item["value"] if max_item else 0
        max_time  = max_item["time"]  if max_item else None
        _, cat, emoji = get_color(max_val)
        print(f"  [{est['nombre']}] Obs:{len(obs)} Pron:{len(pron)} Máx:{max_val:.2f} μg/m³ {emoji} {cat}")

        return {
            "nombre":     est["nombre"],
            "lat":        est["lat"],
            "lng":        est["lng"],
            "buffer_m":   est["buffer_m"],
            "max_val":    max_val,
            "max_time":   max_time,
            "observados": obs,
            "pronostico": pron,
        }
    except Exception as e:
        print(f"  [{est['nombre']}] ❌ Error: {e}")
        return {
            "nombre":     est["nombre"],
            "lat":        est["lat"],
            "lng":        est["lng"],
            "buffer_m":   est["buffer_m"],
            "max_val":    0,
            "max_time":   None,
            "observados": [],
            "pronostico": [],
        }

# ══════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    ahora   = datetime.now(PERU_TZ).replace(tzinfo=None)
    token   = get_token()
    ts_fijo = (int(time.time()) // 300) * 300

    print("=" * 55)
    print("  PM10 Nowcasting · Antamina · 4 Estaciones")
    print(f"  {ahora.strftime('%d/%m/%Y %H:%M:%S')} (Hora Perú)")
    print(f"  Timestamp: {ts_fijo}")
    print("=" * 55)

    # Ejecutar las 4 estaciones en paralelo
    with ThreadPoolExecutor(max_workers=4) as executor:
        futuros = {
            executor.submit(consultar_estacion, est, token, ts_fijo): est
            for est in ESTACIONES
        }
        resultados_dict = {}
        for futuro in as_completed(futuros):
            est = futuros[futuro]
            resultados_dict[est["nombre"]] = futuro.result()

    # Restaurar el orden definido en ESTACIONES
    resultados = [resultados_dict[est["nombre"]] for est in ESTACIONES]

    mapa_render = generar_mapa(resultados)
    html        = generar_html(resultados, mapa_render, ahora)

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n[✓] index.html generado · Corte: {ahora.strftime('%H:%M')}")
