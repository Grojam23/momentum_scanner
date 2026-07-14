"""
Bot de escaneo "Alta Demanda / Baja Oferta" para day trading de small caps.

Basado en los criterios de la imagen del usuario:
 1) Demand: Relative Volume >= X
 2) Demand: % subida en el día >= X
 3) Demand: Debe existir una noticia/evento reciente
 4) Demand: Precio dentro de un rango ($1 - $20 por defecto)
 5) Supply:  Acciones disponibles <= X (float / shares outstanding)
 6) Market Cap <= X (60M por defecto)

Todo configurable desde config.yaml. Sin costo: usa datos públicos de
Yahoo Finance (sin API key) + Finnhub (gratis) para noticias.

Uso:
    python main.py
"""

import os
import sys
import json
import time
import smtplib
import logging
from datetime import datetime, timedelta, timezone, time as dt_time
from zoneinfo import ZoneInfo
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import Header
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml
import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("scanner")

# Silenciamos los mensajes de error internos de yfinance (ej. "possibly
# delisted") para tickers que fallan: ya los manejamos nosotros y reportamos
# un resumen limpio en "Cotizaciones obtenidas para X tickers".
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

load_dotenv()

YAHOO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# 1) Universo de tickers candidatos (gratis, sin API key: Yahoo Finance)
# ---------------------------------------------------------------------------

def get_universe(cfg):
    universe_cfg = cfg["universe"]
    source = universe_cfg.get("source", "tradingview_premarket")
    always_include = universe_cfg.get("always_include", [])

    if source == "custom":
        tickers = list(dict.fromkeys(universe_cfg.get("custom_tickers", [])))
        log.info(f"Universo personalizado: {len(tickers)} tickers")
        return tickers

    if source == "tradingview_premarket":
        tickers = _get_universe_from_tradingview(cfg)
    elif source == "yahoo_custom_screener":
        tickers = _get_universe_from_custom_screener(cfg)
    else:
        # source == "yahoo_screener" (listas predefinidas: day_gainers, most_actives...)
        tickers = _get_universe_from_predefined_lists(universe_cfg)

    if always_include:
        antes = len(tickers)
        tickers = list(dict.fromkeys(tickers) | dict.fromkeys(always_include))
        agregados = len(tickers) - antes
        log.info(
            f"Watchlist 'always_include': {len(always_include)} tickers "
            f"({agregados} no estaban ya en el universo automático)"
        )

    return tickers


def _get_universe_from_tradingview(cfg):
    """Usa tradingview-scraper (librería gratuita, sin API key) para traer
    los tickers que MÁS están subiendo en premarket ahora mismo (categoría
    'pre-market-gainers' de TradingView). Tiene mejor cobertura de microcaps
    ilíquidas que el screener de Yahoo. Si falla o no está instalada la
    librería, cae de vuelta al screener de Yahoo automáticamente.

    IMPORTANTE: se descartan tickers de OTC/Pink Sheets aquí mismo, porque
    Yahoo (de donde sacamos precio/volumen/shares después) casi nunca tiene
    datos confiables para esas acciones. Solo se conservan NASDAQ/NYSE/AMEX,
    que es donde Yahoo funciona bien."""
    universe_cfg = cfg["universe"]
    limit = min(universe_cfg.get("screener_count", 100), 100)
    bolsas_permitidas = {"NASDAQ", "NYSE", "AMEX", "NYSEARCA", "NYSEMKT", "BATS"}

    try:
        from tradingview_scraper.symbols.market_movers import MarketMovers

        market_movers = MarketMovers()
        result = market_movers.scrape(
            market="stocks-usa", category="pre-market-gainers", limit=limit
        )
        items = result.get("data", []) if result else []

        tickers = []
        descartados_otc = 0
        for item in items:
            raw_symbol = item.get("symbol") or item.get("name") or ""
            if ":" in raw_symbol:
                exchange, symbol = raw_symbol.split(":", 1)
                if exchange.strip().upper() not in bolsas_permitidas:
                    descartados_otc += 1
                    continue
            else:
                symbol = raw_symbol
            symbol = symbol.strip()
            if symbol:
                tickers.append(symbol)
        tickers = list(dict.fromkeys(tickers))

        log.info(
            f"TradingView (pre-market-gainers): {len(tickers)} tickers en bolsas principales "
            f"({descartados_otc} descartados por ser OTC/Pink Sheets, sin buena cobertura en Yahoo)"
        )
        if tickers:
            return tickers
        log.warning("TradingView no devolvió tickers, usando Yahoo como respaldo.")
    except ImportError:
        log.warning(
            "Falta instalar 'tradingview-scraper' (pip install -r requirements.txt). "
            "Usando Yahoo como respaldo por ahora."
        )
    except Exception as e:
        log.warning(f"TradingView falló ({e}). Usando Yahoo como respaldo.")

    return _get_universe_from_custom_screener(cfg)


def _get_universe_from_predefined_lists(universe_cfg):
    """Usa las listas predefinidas de Yahoo (day_gainers, most_actives, etc).
    OJO: estas listas suelen estar dominadas por empresas grandes, así que
    casi nunca traerán microcaps por debajo de 60M. Se deja como opción
    alternativa; la opción por defecto es yahoo_custom_screener."""
    tickers = set()
    count = universe_cfg.get("screener_count", 100)

    for scr_id in universe_cfg.get("screener_ids", []):
        url = (
            "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
            f"?formatted=true&scrIds={scr_id}&count={count}"
        )
        try:
            r = requests.get(url, headers=YAHOO_HEADERS, timeout=15)
            r.raise_for_status()
            data = r.json()
            quotes = (
                data.get("finance", {})
                .get("result", [{}])[0]
                .get("quotes", [])
            )
            found = [q["symbol"] for q in quotes if "symbol" in q]
            log.info(f"Screener '{scr_id}': {len(found)} tickers")
            tickers.update(found)
        except Exception as e:
            log.warning(f"No se pudo leer el screener '{scr_id}': {e}")

    tickers = list(tickers)
    log.info(f"Universo total combinado: {len(tickers)} tickers")
    return tickers


def _get_universe_from_custom_screener(cfg):
    """Construye un screener a medida usando la función de screening de
    yfinance (yf.screen / yf.EquityQuery), filtrando directamente por market
    cap y precio (los mismos límites de config.yaml), ordenado por % de
    cambio descendente. Usamos yfinance en vez de llamar la URL de Yahoo
    directo porque yfinance maneja el token/cookie de autenticación que
    Yahoo exige desde hace poco; llamando la URL a mano da 401."""
    c = cfg["criteria"]
    universe_cfg = cfg["universe"]
    size = min(universe_cfg.get("screener_count", 100), 250)

    try:
        query = yf.EquityQuery(
            "and",
            [
                yf.EquityQuery("gte", ["percentchange", c["percent_gain_min"]]),
                yf.EquityQuery("btwn", ["intradaymarketcap", 1, c["market_cap_max"]]),
                yf.EquityQuery("btwn", ["intradayprice", c["price_min"], c["price_max"]]),
                yf.EquityQuery("eq", ["region", "us"]),
            ],
        )
        result = yf.screen(query, sortField="percentchange", sortAsc=False, size=size)
        quotes = result.get("quotes", [])
        tickers = [q["symbol"] for q in quotes if "symbol" in q]
        log.info(
            f"Screener a medida (market cap <= {c['market_cap_max']/1_000_000:.0f}M): "
            f"{len(tickers)} tickers"
        )
        if tickers:
            return tickers
        log.warning("Screener a medida no devolvió tickers, usando listas predefinidas como respaldo.")
    except Exception as e:
        log.warning(f"Screener a medida falló ({e}). Usando listas predefinidas como respaldo.")

    return _get_universe_from_predefined_lists(universe_cfg)




# ---------------------------------------------------------------------------
# 2) Datos de mercado en tiempo real (precio, volumen, market cap, shares)
# ---------------------------------------------------------------------------

US_EASTERN = ZoneInfo("America/New_York")


def _is_premarket_now():
    """True si ahora mismo está en ventana de premarket en Nueva York
    (4:00am - 9:30am hora ET, lunes a viernes)."""
    now_et = datetime.now(US_EASTERN)
    if now_et.weekday() >= 5:  # sábado/domingo
        return False
    return dt_time(4, 0) <= now_et.time() < dt_time(9, 30)


def get_market_regime(cfg):
    """Determina si el mercado está 'caliente' o 'frío' hoy.
    - Si criteria.market_regime es 'hot' o 'cold', se respeta tal cual (manual).
    - Si es 'auto' (por defecto), se mide el VIX en tiempo real vía Yahoo:
      VIX >= vix_hot_threshold -> 'hot', si no -> 'cold'.
    Devuelve (regime, vix_value_o_None)."""
    c = cfg["criteria"]
    mode = c.get("market_regime", "auto")

    if mode in ("hot", "cold"):
        return mode, None

    try:
        vix = yf.Ticker("^VIX")
        vix_price = getattr(vix.fast_info, "last_price", None)
        threshold = c.get("vix_hot_threshold", 20)
        if vix_price is None:
            log.warning("No se pudo leer el VIX; usando 'cold' (más estricto) por defecto.")
            return "cold", None
        regime = "hot" if vix_price >= threshold else "cold"
        return regime, vix_price
    except Exception as e:
        log.warning(f"Error obteniendo el VIX ({e}); usando 'cold' (más estricto) por defecto.")
        return "cold", None


def _fetch_single_quote(symbol):
    """Obtiene los datos de un ticker usando yfinance (maneja el token/cookie
    de Yahoo automáticamente, a diferencia de llamar la URL directo).

    Durante el premarket, usa explícitamente el precio/% de premarket de
    Yahoo (campos preMarketPrice / preMarketChangePercent), en vez de
    depender de que el precio "genérico" (last_price) ya refleje el
    premarket correctamente. Así el % de subida siempre se calcula desde
    el momento en que el ticker es operable, tal como se pidió."""
    try:
        t = yf.Ticker(symbol)
        fi = t.fast_info

        price = getattr(fi, "last_price", None)
        prev_close = getattr(fi, "previous_close", None)
        volume = getattr(fi, "last_volume", None)
        avg_vol = getattr(fi, "three_month_average_volume", None)
        market_cap = getattr(fi, "market_cap", None)
        shares = getattr(fi, "shares", None)

        if not price or not prev_close or not volume or not shares:
            return None

        pct_change = (price - prev_close) / prev_close * 100
        price_source = "regular"

        if _is_premarket_now():
            try:
                info = t.info  # más lento que fast_info, por eso solo se usa en premarket
                pm_price = info.get("preMarketPrice")
                pm_pct = info.get("preMarketChangePercent")
                if pm_price:
                    price = pm_price
                    price_source = "premarket"
                    pct_change = pm_pct if pm_pct is not None else (
                        (price - prev_close) / prev_close * 100
                    )
            except Exception:
                pass  # si falla, seguimos con el valor ya calculado arriba

        return symbol, {
            "symbol": symbol,
            "price": price,
            "percent_change": pct_change,
            "volume": volume,
            "avg_volume_3m": avg_vol,
            "market_cap": market_cap,
            "shares_outstanding": shares,
            "short_name": symbol,
            "price_source": price_source,
        }
    except Exception:
        return None


def get_quotes(tickers, max_workers=10):
    """Devuelve un dict {symbol: {...datos...}} usando yfinance (gratis).

    Se usa threading porque yfinance hace una petición por ticker; con 200+
    tickers, hacerlo uno por uno sería muy lento.
    """
    if not tickers:
        return {}

    quotes = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_single_quote, s): s for s in tickers}
        for future in as_completed(futures):
            result = future.result()
            if result:
                symbol, data = result
                quotes[symbol] = data

    return quotes


# ---------------------------------------------------------------------------
# 3) Noticias / catalizador (Finnhub gratis, con respaldo en NewsAPI)
# ---------------------------------------------------------------------------

def check_recent_split(symbol, lookback_days=90):
    """Revisa si el ticker tuvo un split (o split inverso) reciente. Si lo
    tuvo, el 'volumen promedio de 3 meses' puede incluir datos de ANTES del
    split (con un número de acciones distinto), lo que distorsiona por
    completo el cálculo de volumen relativo. Devuelve un string descriptivo
    si hay split reciente, o None si no."""
    try:
        t = yf.Ticker(symbol)
        splits = t.splits  # pandas Series: fecha -> factor (ej. 0.1 = split inverso 1:10)
        if splits is None or splits.empty:
            return None

        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        recent = splits[splits.index >= cutoff]
        if recent.empty:
            return None

        last_date = recent.index[-1]
        factor = recent.iloc[-1]
        if factor < 1:
            desc = f"split inverso 1:{round(1/factor)}"
        else:
            desc = f"split {round(factor)}:1"
        return f"{desc} el {last_date.strftime('%Y-%m-%d')}"
    except Exception:
        return None


def get_news_for_ticker(symbol, cfg):
    lookback_hours = cfg["criteria"].get("news_lookback_hours", 24)
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    finnhub_key = os.getenv("FINNHUB_API_KEY", "")
    if finnhub_key:
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            from_date = since.strftime("%Y-%m-%d")
            url = (
                "https://finnhub.io/api/v1/company-news"
                f"?symbol={symbol}&from={from_date}&to={today}&token={finnhub_key}"
            )
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            items = r.json()
            if items:
                items.sort(key=lambda x: x.get("datetime", 0), reverse=True)
                top = items[0]
                return {
                    "headline": top.get("headline", "Sin titular"),
                    "source": top.get("source", "Finnhub"),
                    "url": top.get("url", ""),
                }
        except Exception as e:
            log.warning(f"Finnhub news falló para {symbol}: {e}")

    # NewsAPI.org NO se usa como respaldo: sus términos de servicio prohíben
    # el uso en producción en el plan gratuito (solo permiten localhost/dev),
    # así que nunca funcionaría de forma confiable en un cron programado.
    # En su lugar usamos Alpha Vantage News & Sentiment, que sí permite este
    # uso en su plan gratuito (25 llamadas/día, suficiente porque esto solo
    # se llama para los pocos tickers que ya pasaron todos los demás filtros).
    alpha_vantage_key = os.getenv("ALPHA_VANTAGE_API_KEY", "")
    if alpha_vantage_key:
        try:
            url = (
                "https://www.alphavantage.co/query"
                f"?function=NEWS_SENTIMENT&tickers={symbol}&limit=1"
                f"&apikey={alpha_vantage_key}"
            )
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            feed = r.json().get("feed", [])
            if feed:
                top = feed[0]
                return {
                    "headline": top.get("title", "Sin titular"),
                    "source": top.get("source", "Alpha Vantage"),
                    "url": top.get("url", ""),
                }
        except Exception as e:
            log.warning(f"Alpha Vantage news falló para {symbol}: {e}")

    return None


# ---------------------------------------------------------------------------
# 4) Aplicar los 6 criterios
# ---------------------------------------------------------------------------

def evaluate_candidates(quotes, cfg, shares_max, regime, vix_value):
    c = cfg["criteria"]
    pre_matches = []

    premarket_now = _is_premarket_now()
    vol_float_min = (
        c.get("volume_to_float_min_premarket", c["volume_to_float_min"])
        if premarket_now
        else c["volume_to_float_min"]
    )

    stats = {
        "sin_datos": 0,
        "vol_float": 0,
        "porcentaje": 0,
        "precio": 0,
        "market_cap": 0,
        "acciones_disp": 0,
        "sin_noticia": 0,
        "ok": 0,
    }

    # --- Paso 1: filtros baratos (sin llamadas a APIs externas) ---
    for symbol, q in quotes.items():
        price = q.get("price")
        pct = q.get("percent_change")
        vol = q.get("volume")
        mcap = q.get("market_cap")
        shares_out = q.get("shares_outstanding")

        if None in (price, pct, vol, mcap, shares_out) or shares_out == 0:
            stats["sin_datos"] += 1
            continue

        # Rotación de float: cuántas veces se ha negociado el float completo hoy
        vol_to_float = vol / shares_out

        if vol_to_float < vol_float_min:
            stats["vol_float"] += 1
            continue
        if pct < c["percent_gain_min"]:
            stats["porcentaje"] += 1
            continue
        if not (c["price_min"] <= price <= c["price_max"]):
            stats["precio"] += 1
            continue
        if mcap > c["market_cap_max"]:
            stats["market_cap"] += 1
            continue
        if shares_out > shares_max:
            stats["acciones_disp"] += 1
            continue

        pre_matches.append({**q, "volume_to_float": vol_to_float})

    # Rankeamos ANTES de gastar llamadas de noticias, para solo consultar
    # los mejores candidatos (evita golpear el límite gratuito de Finnhub
    # cuando el universo es grande y muchos tickers pasan los filtros baratos)
    pre_matches.sort(key=lambda m: (m["volume_to_float"], m["percent_change"]), reverse=True)

    news_check_limit = c.get("news_check_limit", 15)
    candidatos_a_revisar = pre_matches[:news_check_limit]

    # --- Paso 2: noticias y split, solo para los mejores candidatos ---
    matches = []
    for i, m in enumerate(candidatos_a_revisar):
        symbol = m["symbol"]
        if i > 0:
            time.sleep(1.1)  # Finnhub free tier: ~1 llamada/segundo, evitamos ráfagas

        news = None
        if c.get("require_news", True):
            news = get_news_for_ticker(symbol, cfg)
            if news is None:
                stats["sin_noticia"] += 1
                continue  # no cumple el criterio de "evento de noticia"

        stats["ok"] += 1
        split_warning = check_recent_split(symbol)
        matches.append({**m, "news": news, "split_warning": split_warning})

    vix_txt = f" (VIX={vix_value:.1f})" if vix_value else ""
    log.info(
        f"Régimen de mercado: {regime}{vix_txt} -> límite de float: {shares_max/1_000_000:.0f}M"
    )
    log.info(
        "Diagnóstico de filtrado -> "
        f"sin datos: {stats['sin_datos']}, "
        f"rotación de float insuficiente: {stats['vol_float']}, "
        f"% subida insuficiente: {stats['porcentaje']}, "
        f"fuera de rango de precio: {stats['precio']}, "
        f"market cap muy alto: {stats['market_cap']}, "
        f"acciones disponibles muy altas: {stats['acciones_disp']}, "
        f"pasaron filtros baratos: {len(pre_matches)} "
        f"(se revisó noticia solo para los mejores {len(candidatos_a_revisar)}), "
        f"sin noticia: {stats['sin_noticia']}, "
        f"CUMPLEN TODO: {stats['ok']}"
    )

    matches.sort(key=lambda m: (m["volume_to_float"], m["percent_change"]), reverse=True)
    return matches, vol_float_min


# ---------------------------------------------------------------------------
# 5) Email
# ---------------------------------------------------------------------------

def _sanitize_text(text):
    """Limpia caracteres invisibles/especiales que a veces vienen en textos
    scrapeados (nombres de empresas, titulares de noticias) y que pueden
    romper la codificación del correo: espacios no separables, saltos de
    línea "raros", zero-width spaces, etc. Convierte cualquier resto no-ASCII
    a su forma más cercana en vez de fallar."""
    if text is None:
        return ""
    text = str(text)
    text = (
        text.replace("\xa0", " ")
        .replace("\u200b", "")
        .replace("\u2028", " ")
        .replace("\u2029", " ")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2013", "-")
        .replace("\u2014", "-")
    )
    return text


def build_email_html(matches, cfg, shares_max, regime, vix_value, vol_float_min):
    max_results = cfg["email"].get("max_results", 3)
    top = matches[:max_results]

    rows = ""
    for m in top:
        news = m.get("news")
        if news:
            headline = _sanitize_text(news["headline"])
            source_name = _sanitize_text(news["source"])
            news_html = (
                f'<a href="{news["url"]}" target="_blank">{headline}</a>'
                f'<br><span style="color:#666;font-size:12px;">Fuente: {source_name}</span>'
            )
        else:
            news_html = "Sin noticia detectada"

        mcap_m = (m["market_cap"] or 0) / 1_000_000
        shares_m = (m["shares_outstanding"] or 0) / 1_000_000

        rows += f"""
        <tr>
          <td style="padding:10px;border:1px solid #ddd;"><b>{m['symbol']}</b><br>{m.get('short_name','')}</td>
          <td style="padding:10px;border:1px solid #ddd;">${m['price']:.2f}{' <span style="color:#888;font-size:11px;">(premarket)</span>' if m.get('price_source') == 'premarket' else ''}</td>
          <td style="padding:10px;border:1px solid #ddd;color:#0a7d0a;"><b>+{m['percent_change']:.1f}%</b></td>
          <td style="padding:10px;border:1px solid #ddd;background:#ffe0f0;"><b>{m['volume_to_float']:.1f}x</b>{f'<br><span style="color:#cc0000;font-size:11px;">⚠️ {m["split_warning"]} — dato poco confiable</span>' if m.get('split_warning') else ''}</td>
          <td style="padding:10px;border:1px solid #ddd;">${mcap_m:.1f}M</td>
          <td style="padding:10px;border:1px solid #ddd;background:#fff9c4;">{shares_m:.1f}M</td>
          <td style="padding:10px;border:1px solid #ddd;max-width:260px;">{news_html}</td>
        </tr>
        """

    vix_txt = f" (VIX={vix_value:.1f})" if vix_value else " (manual)"
    regime_label = "🔥 Caliente" if regime == "hot" else "❄️ Frío"

    html = f"""
    <html>
    <body style="font-family:Arial,sans-serif;">
      <h2 style="color:#1a1a1a;">🚀 Escaneo Demanda Alta / Oferta Baja</h2>
      <p style="color:#555;">Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
      <p style="color:#555;">Régimen de mercado: <b>{regime_label}</b>{vix_txt} — límite de float aplicado: {shares_max/1_000_000:.0f}M</p>
      <p>Se encontraron <b>{len(matches)}</b> candidatos que cumplen los criterios.
      Mostrando el top {len(top)}:</p>
      <table style="border-collapse:collapse;width:100%;font-size:14px;">
        <tr style="background:#1a1a1a;color:#fff;">
          <th style="padding:10px;">Ticker</th>
          <th style="padding:10px;">Precio</th>
          <th style="padding:10px;">% Día</th>
          <th style="padding:10px;">Vol/Float</th>
          <th style="padding:10px;">Market Cap</th>
          <th style="padding:10px;">Acciones Disp.</th>
          <th style="padding:10px;">Noticia / Catalizador</th>
        </tr>
        {rows}
      </table>
      <p style="color:#888;font-size:12px;margin-top:20px;">
        Criterios usados: Vol/Float >= {vol_float_min}x |
        % Día >= {cfg['criteria']['percent_gain_min']}% |
        Precio ${cfg['criteria']['price_min']}-${cfg['criteria']['price_max']} |
        Market Cap <= ${cfg['criteria']['market_cap_max']/1_000_000:.0f}M |
        Acciones disponibles <= {shares_max/1_000_000:.0f}M ({regime_label})
      </p>
      <p style="color:#aa0000;font-size:12px;">
        Esto NO es asesoría financiera. Verifica siempre antes de operar.
      </p>
    </body>
    </html>
    """
    return html


def send_email(html_body, cfg, n_matches):
    email_cfg = cfg["email"]
    if not email_cfg.get("enabled", True):
        log.info("Envío de email deshabilitado en config.yaml")
        return

    sender = os.getenv("SENDER_EMAIL")
    password = os.getenv("SENDER_APP_PASSWORD")
    recipient = os.getenv("RECIPIENT_EMAIL") or email_cfg["recipient_email"]

    if not sender or not password:
        log.error("Faltan SENDER_EMAIL o SENDER_APP_PASSWORD en el archivo .env")
        return

    subject = f"{email_cfg.get('subject_prefix','Alerta')} - {n_matches} candidatos ({datetime.now().strftime('%H:%M')})"
    subject = _sanitize_text(subject)
    html_body = _sanitize_text(html_body)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(email_cfg["smtp_server"], email_cfg["smtp_port"]) as server:
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, [recipient], msg.as_bytes())
        log.info(f"Email enviado a {recipient}")
    except Exception as e:
        log.error(f"Error enviando email: {e}")



# ---------------------------------------------------------------------------
# 6) Ciclo principal
# ---------------------------------------------------------------------------

def get_quotes_via_tradingview_screener(cfg):
    """Trae TODO el universo de small caps (filtrado solo por market cap y
    precio) directo desde la API oficial de TradingView (paquete
    'tradingview-screener'), sin exigir todavía el % de subida en la
    consulta misma. El % de subida, rotación de float, etc. se evalúan
    después en evaluate_candidates, con datos frescos de ESE momento. Así no
    se pierde un ticker por estar justo en el límite en el segundo exacto de
    la consulta (ej. sube 9.8% en vez de 10% al momento de pedir los datos).

    A diferencia de yfinance, esto SÍ cubre bien acciones OTC/Pink Sheets,
    que es donde suelen estar los movimientos premarket más explosivos.
    Devuelve {} si el paquete falla o no está instalado (en ese caso se usa
    Yahoo como respaldo)."""
    c = cfg["criteria"]
    universe_cfg = cfg["universe"]
    # Universo completo de small caps: límite mucho más alto que antes,
    # configurable. 1000 cubre cómodamente el universo de acciones US bajo
    # el market cap máximo en un rango de precio típico.
    size = min(universe_cfg.get("screener_count", 1000), 3000)

    try:
        from tradingview_screener import Query, col

        premarket_now = _is_premarket_now()
        change_field = "premarket_change" if premarket_now else "change"
        volume_field = "premarket_volume" if premarket_now else "volume"

        query = (
            Query()
            .select("name", "close", change_field, volume_field, "market_cap_basic", "exchange")
            .where(
                col("market_cap_basic").between(1, c["market_cap_max"]),
                col("close").between(c["price_min"], c["price_max"]),
            )
            .order_by(change_field, ascending=False)
            .limit(size)
        )
        _, df = query.get_scanner_data()

        quotes = {}
        for _, row in df.iterrows():
            ticker_full = str(row.get("ticker", ""))
            symbol = ticker_full.split(":")[-1].strip()
            close = row.get("close")
            pct = row.get(change_field)
            vol = row.get(volume_field)
            mcap = row.get("market_cap_basic")

            if (
                not symbol
                or pd.isna(close) or close == 0
                or pd.isna(pct)
                or pd.isna(vol)
                or pd.isna(mcap) or not mcap
            ):
                continue

            # Acciones en circulación APROXIMADAS: TradingView no siempre expone
            # 'shares_outstanding' directo en este endpoint, así que lo derivamos
            # de market_cap / precio. Es una aproximación razonable, no exacta.
            shares_approx = mcap / close

            quotes[symbol] = {
                "symbol": symbol,
                "price": close,
                "percent_change": pct,
                "volume": vol,
                "avg_volume_3m": None,
                "market_cap": mcap,
                "shares_outstanding": shares_approx,
                "short_name": row.get("name", symbol),
                "price_source": "premarket" if premarket_now else "regular",
            }

        log.info(
            f"TradingView Screener: {len(quotes)} tickers en el universo de small caps "
            f"(sin filtrar aún por %; incluye OTC/Pink Sheets, modo "
            f"{'premarket' if premarket_now else 'regular'})"
        )
        return quotes
    except ImportError:
        log.warning(
            "Falta instalar 'tradingview-screener' (pip install -r requirements.txt). "
            "Usando Yahoo como respaldo por ahora."
        )
        return {}
    except Exception as e:
        log.warning(f"TradingView Screener falló ({e}). Usando Yahoo como respaldo.")
        return {}


def _merge_always_include(quotes, cfg):
    """Se asegura de que los tickers en universe.always_include SIEMPRE se
    evalúen, sin importar si el screener automático (TradingView o Yahoo) los
    trajo ese ciclo. Usa yfinance directo por ticker (confiable para
    NASDAQ/NYSE/AMEX; si el ticker es OTC puro, puede fallar igual)."""
    always = cfg["universe"].get("always_include", [])
    if not always:
        return quotes

    faltantes = [t for t in always if t not in quotes]
    if not faltantes:
        return quotes

    log.info(
        f"Watchlist 'always_include': revisando {len(faltantes)} tickers que no "
        f"vinieron en el universo automático ({', '.join(faltantes)})"
    )
    for symbol in faltantes:
        result = _fetch_single_quote(symbol)
        if result:
            sym, data = result
            quotes[sym] = data
        else:
            log.warning(
                f"'{symbol}' de always_include no se pudo obtener vía Yahoo "
                "(podría ser OTC, o el símbolo estar mal escrito)."
            )
    return quotes


SENT_LOG_PATH = "sent_alerts.json"


def _load_sent_log():
    """Carga el historial de qué tickers se enviaron y cuándo. Vive en un
    archivo dentro del repo (sent_alerts.json) para que persista entre
    corridas de GitHub Actions (cada corrida arranca 'desde cero', así que
    sin esto no habría forma de recordar qué ya se avisó)."""
    try:
        with open(SENT_LOG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_sent_log(data):
    try:
        with open(SENT_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
    except Exception as e:
        log.warning(f"No se pudo guardar el historial de alertas enviadas: {e}")


def _parse_ts(ts_str):
    try:
        return datetime.fromisoformat(ts_str)
    except Exception:
        return None


def filter_recently_sent(matches, cfg):
    """Descarta de la lista los tickers que ya se enviaron por correo hace
    menos de email.cooldown_hours. El escaneo/evaluación sigue corriendo
    siempre sobre TODO el universo cada ciclo; esto solo filtra qué se
    manda por correo, para no repetir la misma alerta cada 5 minutos."""
    cooldown_hours = cfg["email"].get("cooldown_hours", 4)
    if cooldown_hours <= 0:
        return matches  # cooldown desactivado: siempre reenvía

    sent_log = _load_sent_log()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=cooldown_hours)

    fresh = []
    for m in matches:
        last_sent = _parse_ts(sent_log.get(m["symbol"], ""))
        if last_sent and last_sent > cutoff:
            continue  # ya se avisó hace poco, se omite del correo
        fresh.append(m)
    return fresh


def mark_as_sent(matches, cfg):
    """Registra los tickers recién enviados con la hora actual, y limpia
    entradas viejas (>48h) para que el archivo no crezca indefinidamente."""
    cooldown_hours = cfg["email"].get("cooldown_hours", 4)
    if cooldown_hours <= 0:
        return

    sent_log = _load_sent_log()
    now = datetime.now(timezone.utc)
    for m in matches:
        sent_log[m["symbol"]] = now.isoformat()

    cleanup_cutoff = now - timedelta(hours=48)
    sent_log = {
        sym: ts for sym, ts in sent_log.items()
        if _parse_ts(ts) and _parse_ts(ts) > cleanup_cutoff
    }
    _save_sent_log(sent_log)


def run_once(cfg):
    log.info("=== Iniciando escaneo ===")

    regime, vix_value = get_market_regime(cfg)
    shares_max = (
        cfg["criteria"]["shares_outstanding_max_hot"]
        if regime == "hot"
        else cfg["criteria"]["shares_outstanding_max_cold"]
    )

    quotes = get_quotes_via_tradingview_screener(cfg)
    if not quotes:
        log.info("Usando universo + Yahoo como respaldo (sin cobertura OTC).")
        tickers = get_universe(cfg)
        quotes = get_quotes(tickers)
        log.info(f"Cotizaciones obtenidas para {len(quotes)} tickers")

    quotes = _merge_always_include(quotes, cfg)

    matches, vol_float_min = evaluate_candidates(quotes, cfg, shares_max, regime, vix_value)
    log.info(f"Candidatos que cumplen TODOS los criterios: {len(matches)}")

    matches_a_enviar = filter_recently_sent(matches, cfg)
    omitidos = len(matches) - len(matches_a_enviar)
    if omitidos:
        cooldown_h = cfg["email"].get("cooldown_hours", 4)
        log.info(
            f"{omitidos} candidato(s) NO se re-envían (ya avisados hace menos de {cooldown_h}h)"
        )

    if matches_a_enviar:
        html = build_email_html(matches_a_enviar, cfg, shares_max, regime, vix_value, vol_float_min)
        send_email(html, cfg, len(matches_a_enviar))
        enviados = matches_a_enviar[: cfg["email"].get("max_results", 3)]
        mark_as_sent(enviados, cfg)
    elif not matches and cfg["email"].get("send_even_if_no_matches"):
        html = "<p>No se encontraron candidatos que cumplan todos los criterios en este ciclo.</p>"
        send_email(html, cfg, 0)

    if not matches:
        log.info("Sin candidatos que cumplan todos los criterios en este ciclo.")
    elif not matches_a_enviar:
        log.info("Había candidatos, pero todos ya se habían avisado recientemente (cooldown).")

    log.info("=== Escaneo finalizado ===")


def debug_ticker(symbol, cfg):
    """Modo diagnóstico: explica, criterio por criterio, por qué un ticker
    específico sí o no aparecería en el correo. Uso: python main.py --debug MIMI"""
    c = cfg["criteria"]
    symbol = symbol.upper()

    regime, vix_value = get_market_regime(cfg)
    shares_max = (
        c["shares_outstanding_max_hot"] if regime == "hot" else c["shares_outstanding_max_cold"]
    )
    vix_txt = f" (VIX={vix_value:.1f})" if vix_value else " (manual)"
    print(f"\n=== Diagnóstico de {symbol} ===")
    print(f"Régimen de mercado: {regime}{vix_txt} -> límite de float: {shares_max/1_000_000:.0f}M\n")

    result = _fetch_single_quote(symbol)
    if result is None:
        print(
            "❌ No se pudo obtener cotización para este ticker vía Yahoo/yfinance "
            "(precio, cierre anterior, volumen o acciones en circulación faltantes).\n"
            "⚠️  Importante: este modo --debug usa Yahoo, que tiene cobertura floja "
            "para OTC/Pink Sheets. El escaneo normal (main.py sin --debug) usa "
            "TradingView primero, que SÍ cubre bien OTC — así que es posible que "
            "este ticker sí aparezca en el correo real aunque el debug falle aquí."
        )
        return

    _, q = result
    price = q["price"]
    pct = q["percent_change"]
    vol = q["volume"]
    mcap = q["market_cap"]
    shares_out = q["shares_outstanding"]
    vol_to_float = (vol / shares_out) if shares_out else None

    def check(label, ok, detail):
        icon = "✅" if ok else "❌"
        print(f"{icon} {label}: {detail}")

    print(f"Precio actual: ${price:.2f} (fuente: {q.get('price_source', 'regular')})")
    print(f"Volumen hoy: {vol:,} | Acciones en circulación: {shares_out:,}\n" if shares_out else f"Volumen hoy: {vol:,}\n")

    premarket_now = _is_premarket_now()
    vol_float_min = (
        c.get("volume_to_float_min_premarket", c["volume_to_float_min"])
        if premarket_now
        else c["volume_to_float_min"]
    )

    check(
        "Rotación de float (Vol/Float)",
        vol_to_float is not None and vol_to_float >= vol_float_min,
        f"{vol_to_float:.2f}x (necesitas >= {vol_float_min}x, {'premarket' if premarket_now else 'regular'})" if vol_to_float else "sin dato",
    )
    check(
        "% de subida hoy",
        pct >= c["percent_gain_min"],
        f"{pct:.2f}% (necesitas >= {c['percent_gain_min']}%)",
    )
    check(
        "Rango de precio",
        c["price_min"] <= price <= c["price_max"],
        f"${price:.2f} (rango permitido: ${c['price_min']}-${c['price_max']})",
    )
    check(
        "Market Cap",
        mcap is not None and mcap <= c["market_cap_max"],
        f"${mcap/1_000_000:.1f}M (límite: ${c['market_cap_max']/1_000_000:.0f}M)" if mcap else "sin dato",
    )
    check(
        "Acciones disponibles",
        shares_out is not None and shares_out <= shares_max,
        f"{shares_out/1_000_000:.1f}M (límite: {shares_max/1_000_000:.0f}M, régimen {regime})" if shares_out else "sin dato",
    )

    news = get_news_for_ticker(symbol, cfg)
    check(
        "Noticia/catalizador detectado",
        news is not None,
        news["headline"] if news else "ninguna noticia encontrada por Finnhub/Alpha Vantage",
    )

    split_warning = check_recent_split(symbol)
    if split_warning:
        print(
            f"\n⚠️  ATENCIÓN: {symbol} tuvo un {split_warning}. Esto puede "
            "afectar la fiabilidad de 'acciones en circulación' si el dato de "
            "Yahoo no está bien ajustado post-split. Compara siempre contra "
            "otra fuente (Webull, TradingView) para tickers con splits recientes."
        )

    print(
        "\nNota: aunque hoy TODO salga en verde, en corridas pasadas puede "
        "haber fallado un criterio distinto en ese momento exacto (el precio, "
        "% y volumen cambian minuto a minuto). Este diagnóstico es del "
        "instante en que lo corres, no del momento histórico que preguntas.\n"
    )


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--debug":
        cfg = load_config()
        debug_ticker(sys.argv[2], cfg)
        return

    cfg = load_config()
    mode = cfg["schedule"].get("run_mode", "once")

    if mode == "once":
        run_once(cfg)
    elif mode == "loop":
        interval = cfg["schedule"].get("interval_minutes", 15)
        log.info(f"Modo loop activado. Corriendo cada {interval} minutos. Ctrl+C para detener.")
        while True:
            try:
                run_once(cfg)
            except Exception as e:
                log.error(f"Error en el ciclo: {e}")
            time.sleep(interval * 60)
    else:
        log.error(f"run_mode desconocido: {mode}. Usa 'once' o 'loop'.")
        sys.exit(1)


if __name__ == "__main__":
    main()
