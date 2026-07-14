# Guía de Instalación y Uso — Bot "Demanda Alta / Oferta Baja"

Este bot escanea el mercado buscando acciones (small caps) que cumplan los 5
criterios de tu imagen, más el límite de Market Cap de 60M que pediste, y te
envía un correo con el Top 3 (configurable) y la noticia que causó el movimiento.

Es 100% gratis: usa datos públicos de Yahoo Finance (sin API key) y Finnhub
(plan gratuito) para noticias.

---

## 1. Qué archivos tienes

```
momentum_scanner/
├── main.py                    → el bot (no necesitas tocarlo)
├── config.yaml                → AQUÍ configuras todos los criterios
├── .env.example                → plantilla de tus claves/contraseñas
├── requirements.txt           → librerías necesarias
├── .github/workflows/scan.yml → para correrlo gratis en GitHub Actions
└── INSTALL.md                  → este archivo
```

---

## 2. Instalación local (para probarlo en tu computador)

### 2.1 Requisitos
- Python 3.9 o superior instalado (`python3 --version` en terminal).

### 2.2 Pasos

```bash
# 1) Entra a la carpeta del proyecto
cd momentum_scanner

# 2) Crea un entorno virtual (recomendado)
python3 -m venv .venv
source .venv/bin/activate        # En Windows: .venv\Scripts\activate

# 3) Instala las dependencias
pip install -r requirements.txt

# 4) Copia el archivo de variables de entorno
cp .env.example .env
```

### 2.3 Configura tu `.env`
Abre el archivo `.env` y completa:

- `SENDER_EMAIL`: el correo desde el cual se enviarán las alertas (recomendado
  crear uno nuevo tipo `mibot.alertas@gmail.com`).
- `SENDER_APP_PASSWORD`: una "Contraseña de aplicación" de Gmail (ver sección 3).
- `FINNHUB_API_KEY`: tu clave de Finnhub (la misma que usas en tu bot pre-market).
  Si no la tienes, créala gratis en https://finnhub.io/register
- `ALPHA_VANTAGE_API_KEY` (opcional pero recomendado): segunda fuente de
  noticias, solo se usa si Finnhub no encuentra nada. Créala gratis en
  https://www.alphavantage.co/support/#api-key (no pide tarjeta). Ojo: NO
  uses NewsAPI.org — su plan gratuito prohíbe usarlo fuera de localhost, así
  que nunca funcionaría de forma confiable en este bot.

### 2.4 Configura `config.yaml`
Abre `config.yaml` y ajusta lo que quieras, por ejemplo:

```yaml
criteria:
  relative_volume_min: 5.0      # súbelo o bájalo según qué tan estricto quieres ser
  percent_gain_min: 10.0
  price_min: 1.0
  price_max: 20.0
  market_cap_max: 60000000      # 60 millones, tal como pediste
  shares_outstanding_max: 10000000
```

Y pon tu correo de destino:

```yaml
email:
  recipient_email: "tu_correo_personal@gmail.com"
```

### 2.5 Ejecutar

```bash
python main.py
```

Si todo está bien configurado, verás logs en la terminal y te llegará un
correo si hay candidatos que cumplen los criterios.

---

## 3. Cómo crear la "Contraseña de aplicación" de Gmail (gratis, 2 minutos)

Gmail no permite usar tu contraseña normal desde código, por seguridad.

1. Ve a https://myaccount.google.com/security
2. Activa la "Verificación en 2 pasos" si no la tienes activada (obligatorio
   para poder generar contraseñas de aplicación).
3. Busca "Contraseñas de aplicaciones" (o entra directo a
   https://myaccount.google.com/apppasswords).
4. Crea una nueva, ponle un nombre como "Momentum Scanner", y Google te dará
   un código de 16 letras.
5. Copia ese código (sin espacios) en `SENDER_APP_PASSWORD` dentro de tu `.env`.

---

## 4. Cómo hacer que corra automáticamente cada cierto tiempo (GRATIS)

Tienes dos opciones. Te recomiendo la **Opción A** porque no depende de que
tu computador esté encendido.

### Opción A: GitHub Actions (recomendado, gratis, corre en la nube)

1. Crea un repositorio nuevo en GitHub (puede ser privado).
2. Sube esta carpeta completa a ese repositorio:
   ```bash
   git init
   git add .
   git commit -m "Momentum scanner bot"
   git branch -M main
   git remote add origin https://github.com/TU_USUARIO/TU_REPO.git
   git push -u origin main
   ```
   ⚠️ El archivo `.env` NO se sube (ya está en `.gitignore`), así que tus
   claves están seguras.
3. En GitHub, ve a tu repositorio → **Settings → Secrets and variables →
   Actions → New repository secret**, y crea estos 4 secretos:
   - `SENDER_EMAIL`
   - `SENDER_APP_PASSWORD`
   - `FINNHUB_API_KEY`
   - `ALPHA_VANTAGE_API_KEY` (puedes dejarlo vacío si no lo usas)
4. Ve a la pestaña **Actions** de tu repositorio. Ya debería aparecer el
   workflow "Momentum Scanner". Dale click a "Run workflow" para probarlo
   manualmente.
5. Automático: el archivo `.github/workflows/scan.yml` ya trae programado
   que corra **cada 20 minutos, de 13:00 a 16:00 hora de España (lunes a
   viernes)**:

   ```yaml
   - cron: "*/20 11-13 * * 1-5"   # 13:00, 13:20, 13:40, 14:00 ... 15:40 España
   - cron: "0 14 * * 1-5"          # 16:00 España en punto (cierre del rango)
   ```

   Formato: `minuto hora díaDelMes mes díaDeLaSemana`, y **siempre en hora UTC**
   (GitHub Actions no usa tu zona horaria). Por eso 13:00-16:00 España se
   traduce a 11:00-14:00 UTC (España está en CEST = UTC+2 en horario de verano).

   ⚠️ **Importante sobre el cambio de horario**: cuando España pase a horario
   de invierno (CET = UTC+1, a fines de octubre), estas mismas horas UTC
   correrán una hora antes en tu reloj (o sea, el bot sonaría de 12:00 a 15:00
   hora España en vez de 13:00 a 16:00). Si quieres que sea siempre exacto a
   las 13:00-16:00 España sin importar la época del año, tienes dos opciones:
   - Ajustar manualmente el cron dos veces al año (fin de marzo y fin de
     octubre, sumando/restando 1 hora), o
   - Usar la **Opción B (cron local o Task Scheduler)** de la sección 4, que
     respeta automáticamente tu zona horaria de España todo el año.

   Otros ejemplos de frecuencia (siempre en UTC):
   - Cada 10 minutos: `*/10 11-13 * * 1-5`
   - Cada 30 minutos: `*/30 11-13 * * 1-5`

   Nota: GitHub Actions no garantiza el minuto exacto en el plan gratuito,
   puede tener unos minutos de retraso en horas de mucho tráfico.

### Opción B: Cron local (si prefieres correrlo desde tu propio computador)

En Mac/Linux, edita tu crontab:
```bash
crontab -e
```
Y agrega (ejemplo cada 15 minutos, de 9:30am a 4pm hora de Nueva York):
```
*/15 9-16 * * 1-5 cd /ruta/a/momentum_scanner && /ruta/a/.venv/bin/python main.py >> scanner.log 2>&1
```

En Windows, usa el "Programador de tareas" (Task Scheduler) y crea una
tarea que ejecute:
```
C:\ruta\a\momentum_scanner\.venv\Scripts\python.exe C:\ruta\a\momentum_scanner\main.py
```
repitiendo cada 15 minutos.

---

## 5. Cómo interpretar el correo que te llega

Cada correo trae una tabla con, como máximo, los 3 mejores candidatos
(configurable en `email.max_results`), ordenados por volumen relativo y % de
subida. Por cada uno verás:

| Columna | Qué significa |
|---|---|
| Ticker | Símbolo de la acción |
| Precio | Precio actual |
| % Día | Cuánto ha subido hoy |
| Vol. Relativo | Cuántas veces por sobre su volumen promedio está operando hoy (ej: 6.2x) |
| Market Cap | Capitalización de mercado, en millones |
| Acciones Disp. | Acciones en circulación (proxy del float/supply, en millones) |
| Noticia / Catalizador | El titular más reciente relacionado, con link a la fuente |

---

## 5.1 Si ya instalaste antes y ahora ves errores "401 Unauthorized"

Yahoo Finance cambió su endpoint de cotizaciones y ahora exige un token de
autenticación (crumb/cookie) que la librería `yfinance` sí sabe generar
automáticamente. Si instalaste el proyecto antes de este cambio, solo
necesitas actualizar dependencias y volver a correr:

```bash
source .venv/bin/activate
pip install -r requirements.txt --upgrade
python main.py
```

El endpoint de "universo" (las listas de day_gainers, most_actives, etc.)
no se vio afectado, por eso en los logs ves que sí encuentra los tickers
candidatos; el error 401 era solo al pedir el precio/volumen de cada uno.

---

## 5.2 Si te sale "Cotizaciones obtenidas" pero luego "0 candidatos"

Esto casi nunca es un error — significa que el bot corrió bien pero, en ese
momento del mercado, ningún ticker cumplió los 6 criterios a la vez (es
normal, sobre todo con criterios estrictos como 5x volumen + 10% de subida
+ menos de 60M de market cap: no siempre hay algo así corriendo).

Para saber exactamente en qué criterio se están cayendo tus candidatos,
ahora el bot imprime una línea de diagnóstico en cada corrida, por ejemplo:

```
Diagnóstico de filtrado -> sin datos: 4, volumen relativo insuficiente: 180,
% subida insuficiente: 30, fuera de rango de precio: 10, market cap muy alto: 3,
acciones disponibles muy altas: 1, sin noticia: 0, CUMPLEN TODO: 0
```

Con eso puedes ver si, por ejemplo, casi todos se caen en "market cap muy
alto" (significa que tu universo de tickers no trae suficientes microcaps —
revisa la sección 5.3) o en "sin noticia" (puedes probar con
`require_news: false` temporalmente para verificar que el resto del filtro
sí funciona).

⚠️ **Importante sobre cómo leer estos números**: los contadores son
**secuenciales**, no independientes. Un ticker se cuenta en el primer
criterio que no cumple y ahí se detiene — no sigue evaluando el resto. Por
eso, si ves "volumen relativo insuficiente: 222" y "market cap muy alto: 0",
NO significa que todos tengan buen market cap; significa que esos 222 ni
siquiera llegaron a evaluarse en market cap porque ya se cayeron antes, en
volumen relativo.

**Prueba siempre dentro de tu ventana de mercado configurada.** Si corres el
bot fuera de horario (de madrugada, hora España) vas a ver casi todo caerse
en "volumen relativo insuficiente" — es normal y esperado: a esa hora casi
no hay volumen acumulado todavía, así que ningún ticker puede mostrar 5x de
su promedio. Prueba entre las 13:00 y 16:00 hora España (tu ventana
configurada en `.github/workflows/scan.yml`), que es cuando el mercado
americano ya está en pre-market activo / recién abierto.

Si quieres verificar que todo el pipeline (universo → cotizaciones → email)
funciona de punta a punta sin esperar a que se den las condiciones reales,
baja temporalmente los umbrales en `config.yaml`, por ejemplo:
```yaml
criteria:
  relative_volume_min: 1.2
  percent_gain_min: 1.0
```
Corre `python main.py`, confirma que te llega el correo, y luego vuelve a
subir los valores a los reales (5.0 y 10.0).

## 5.3 Por qué cambiamos la fuente del universo (`universe.source`)

**Actualización:** la fuente por defecto ahora es `tradingview_premarket`, que
usa la librería gratuita `tradingview-scraper` (ya incluida en
`requirements.txt`) y su categoría **"pre-market-gainers"** — trae
directamente los tickers que más están subiendo en premarket ahora mismo,
con mejor cobertura de microcaps ilíquidas que el screener de Yahoo. Si por
algún motivo falla (librería no instalada, TradingView caído, etc.), el bot
cae automáticamente de vuelta al screener de Yahoo, sin que tengas que hacer
nada.

Si además quieres asegurarte de que ciertos tickers específicos que tú sigues
personalmente (por ejemplo, los que ves en Webull) siempre se evalúen, sin
importar si el screener automático los trae ese día, agrégalos a:

```yaml
universe:
  always_include: ["SOBR", "MIMI"]
```

Estos se suman al universo automático en cada corrida, no lo reemplazan.



Las listas predefinidas de Yahoo (`day_gainers`, `most_actives`,
`small_cap_gainers`) están dominadas por empresas grandes (TSLA, NVDA,
NFLX...) y casi nunca incluyen microcaps por debajo de 60M de market cap —
por eso el filtro de market cap descartaba casi todo. Por defecto ahora
`config.yaml` usa `source: yahoo_custom_screener`, que arma una consulta a
medida en Yahoo Finance filtrando directamente por tu `market_cap_max`,
tu rango de precio y tu `percent_gain_min`, así que sí trae microcaps
reales que están subiendo hoy. Si por algún motivo ese screener falla, el
bot cae automáticamente de vuelta a las listas predefinidas como respaldo.

---

## 5.4 "¿Por qué no me trajo tal ticker?" — Modo diagnóstico de un solo ticker

Si ves que una acción tuvo un movimiento fuerte hoy pero el bot no te avisó
de ella, puedes preguntarle directamente por qué:

```bash
python main.py --debug MIMI
```

Esto te muestra, criterio por criterio, en qué está el ticker **ahora mismo**
(✅/❌ en volumen relativo, % de subida, precio, market cap, acciones
disponibles y noticia). Ejemplo de salida:

```
=== Diagnóstico de MIMI ===

Precio actual: $2.70
Volumen hoy: 2,070,000 | Volumen promedio 3m: 4,910,000

❌ Volumen relativo: 0.42x (necesitas >= 5.0x)
❌ % de subida hoy: 6.00% (necesitas >= 10.0%)
✅ Rango de precio: $2.70 (rango permitido: $1.0-$20.0)
✅ Market Cap: $5.8M (límite: $60M)
✅ Acciones disponibles: 2.2M (límite: 10.0M)
✅ Noticia/catalizador detectado: Mint's Axonex subsidiary debuts NEX robot...
```

⚠️ Importante: este diagnóstico es del **momento exacto en que lo corres**,
no de cuando corrió la alerta original — precio, % y volumen cambian minuto
a minuto. Además, este modo evalúa el ticker directo, sin pasar por el paso
de "universo" (screener); si el problema fue que el ticker ni siquiera
apareció en la lista de candidatos de ese ciclo (algo posible con microcaps
muy nuevos o poco indexados por Yahoo), este comando no lo va a mostrar —
solo te dice si el ticker cumple o no los criterios en sí.

---

## 6. Limitaciones importantes (para que no te sorprendan)

- **Float vs. Shares Outstanding**: el criterio #5 de tu imagen habla de
  "acciones disponibles para operar" (float público). Los datos de float
  exacto (sin contar insiders/institucionales bloqueados) casi siempre son de
  pago. Este bot usa "shares outstanding" (acciones totales en circulación)
  como aproximación gratuita, que suele ser un número similar o algo mayor al
  float real. Si más adelante quieres precisión exacta de float, se puede
  integrar una API paga (ej. FMP plan pago, Intrinio, Benzinga).
- **Fuente de datos de mercado**: se usa un endpoint público de Yahoo Finance
  que no es una API oficial documentada. Es gratis y confiable en la
  práctica, pero Yahoo podría cambiarlo sin aviso. Si un día deja de
  funcionar, avísame y lo migramos a otra fuente (ej. Finnhub/Polygon con
  límites gratuitos más bajos).
- **Rate limits de Finnhub**: el plan gratuito permite 60 llamadas/minuto,
  suficiente para este uso.
- Este bot es una herramienta de apoyo, **no es asesoría financiera**.
  Siempre verifica manualmente antes de operar.

---

## 7. Personalización rápida (resumen)

Todo se controla desde `config.yaml`, sin tocar código:

- Cambiar el % mínimo de subida → `criteria.percent_gain_min`
- Cambiar el volumen relativo mínimo → `criteria.relative_volume_min`
- Cambiar el rango de precio → `criteria.price_min` / `price_max`
- Cambiar el market cap máximo → `criteria.market_cap_max`
- Cambiar cuántas acciones disponibles como máximo → `criteria.shares_outstanding_max`
- Exigir o no noticia → `criteria.require_news` (true/false)
- Cambiar destinatario del correo → `email.recipient_email`
- Cambiar cuántos tickers manda por correo → `email.max_results`
- Cambiar la frecuencia (si usas GitHub Actions) → `.github/workflows/scan.yml`,
  línea `cron`
- Cambiar la frecuencia (si usas modo loop local) → `schedule.interval_minutes`
  y `schedule.run_mode: loop`
