# BotBet (valuebet-bot)

**BotBet** — el nombre juega con **Bot** (todo el pipeline corre solo) + **Bet**
(apuestas) — es un bot de **análisis** de apuestas deportivas de valor (value
betting) de **fútbol de todas las ligas del mundo** (Premier League, Champions
League, Liga MX, Primera A colombiana, lo que sea que cubra el proveedor de
cuotas), evaluando las cuotas que ofrece **Betplay** contra **Bet365** como
referencia (ver "Sobre el libro de referencia" más abajo — Pinnacle, el libro
"sharp" original del diseño, no está disponible en el proveedor de cuotas
usado). **No coloca apuestas automáticamente.** Cada mañana a las 7:00 (hora
Bogotá) te envía por Telegram los 10 picks con **mayor EV** encontrados ese
día — nunca partidos al azar, siempre el resultado de ordenar por valor
esperado todo lo que se pudo evaluar —, al día siguiente verifica solo si
ganaron o perdieron y guarda el histórico, genera una imagen lista para redes
sociales con los picks del día y otra con los resultados de ayer, y las
publica automáticamente en la cuenta de Instagram de BotBet (ver la sección
"Identidad de marca e Instagram" más abajo). Tú decides y colocas la apuesta
manualmente en la app/web del operador.

Corre **gratis, sin servidor propio**, en GitHub Actions (ver más abajo por qué).

El nombre del paquete de Python y el repo siguen llamándose `valuebet-bot`
por continuidad del código — `BotBet` es la marca de cara al público (el
Telegram y, sobre todo, Instagram).

## Por qué funciona así (y no como un bot que apuesta solo)

Ninguna casa de apuestas con licencia de Coljuegos (Rushbet, Wplay, Betplay,
Yajuego, Codere Colombia, Corredor Empire, etc.) publica una API pública para
que un usuario normal coloque apuestas de forma programática. Todas son
"sportsbooks" de cuota fija, no exchanges (a diferencia de Betfair, que sí
tiene API de trading pero no opera en Colombia). Sus términos de servicio
prohíben explícitamente el uso de bots/automatización para apostar, y
detectarlo puede significar suspensión de la cuenta y retención de fondos.
Automatizar la colocación implicaría simular al usuario (navegador automatizado
o ingeniería inversa de la app privada), lo cual viola esos términos y puede
rozar la Ley 1273 de 2009 (delitos informáticos) si se interpreta como acceso
no autorizado.

Este proyecto automatiza la parte que **sí** es legítima y estándar en la
industria: recolectar cuotas públicas, calcular dónde hay valor estadístico
(EV positivo) y avisarte. La decisión y el clic final siempre son tuyos.

## Alcance: fútbol, todas las ligas del mundo

`odds_provider.sports` está fijado a `["football"]`, y `odds_provider.leagues`
está vacío (`[]`) **a propósito**: eso significa "todas las ligas de fútbol
para las que el proveedor tenga datos", no solo la Primera A colombiana. Si
alguna vez quieres restringir a ligas específicas (por ejemplo, solo las
grandes europeas), pones sus slugs ahí — pero el valor por defecto del
proyecto es cobertura mundial. Si en algún momento quieres ampliar a otros
deportes además de fútbol, es una línea más en `sports` — pero el diseño
actual (mensajes, imagen, liquidación) asume fútbol (marcador home/away, 1x2,
totales, hándicap).

### Sobre Wplay: por ahora no está cubierta

El pedido original era evaluar Wplay y Betplay, pero **odds-api.io no tiene a
Wplay entre sus casas soportadas** — confirmado con `GET /bookmakers` (con tu
propia API key, no solo mirando su sitio) sobre la cuenta real: Wplay no
aparece en absoluto en el listado; Betplay sí, con ese nombre exacto. Pedirle
cuotas de un bookmaker que no reconoce hace que `/odds/multi` devuelva 400 en
vez de simplemente ignorarlo, así que si lo dejas en
`target_bookmakers` el job falla en vez de solo omitir esa casa — por eso
`config.example.yaml` trae únicamente `["Betplay"]`. Si más adelante
odds-api.io agrega a Wplay, agregarla es una palabra en esa lista. La
alternativa mientras tanto sería sumar un segundo proveedor de cuotas que sí
cubra Wplay e implementar la interfaz `OddsProvider` para él (ver "Extender
el proyecto").

### Sobre el libro de referencia: por qué Bet365 y no Pinnacle

El diseño original de este proyecto usaba **Pinnacle** como libro "sharp" de
referencia para el cálculo de devig/EV — es el estándar de la industria para
esto, porque su margen es bajo y sus cuotas se consideran de las más
eficientes/calibradas del mercado. El problema, descubierto ya en producción
(no en el diseño): **Pinnacle tampoco está en el catálogo de odds-api.io**
— no es un tema de nombre mal escrito ni de plan pago, `GET /bookmakers`
simplemente no lo lista entre los más de 250 bookmakers que sí cubre.

Sustituto elegido: **Bet365**. No es un libro "sharp" en sentido estricto
(tiene más margen que Pinnacle y no es tan consistentemente eficiente), pero
es uno de los de mayor volumen y liquidez entre los que odds-api.io sí cubre
en el nivel gratuito — la mejor aproximación disponible sin pagar. Esto tiene
una consecuencia real que hay que tener presente: **el EV% calculado es una
estimación más ruidosa que si se usara Pinnacle** — el "colchón" del
`min_ev_pct` (3% por defecto) importa más aquí que si tuvieras un libro
verdaderamente sharp de referencia, porque parte de ese EV puede ser solo
margen residual de Bet365 mal estimado, no valor real. Si en algún momento
quieres una referencia más rigurosa, las opciones son: pagar un plan de
odds-api.io que sí incluya libros "sharp" (Circa, Betfair Exchange, etc. —
ver su página de precios) y cambiarlos en `reference_bookmakers`, o promediar
varios libros recreativos en vez de uno solo (requeriría un cambio en
`devig.py`, hoy asume un único libro de referencia por defecto aunque
`min_reference_books` ya permite configurar cuántos se exigen como mínimo).

El plan gratuito de odds-api.io además solo permite **2 bookmakers propios en
total** (target + reference combinados) — con Betplay + Bet365 ya se ocupan
los dos cupos, así que agregar una tercera casa (otra target o otra
reference) probablemente exige subir de plan.

### Mercados soportados: por ahora solo 1X2 (h2h)

Otro problema real descubierto en producción, más serio que los dos
anteriores: los primeros picks reales que llegaron traían **EV de +300% a
+760%** en mercados de "Total de goles" — cifras que no son valor real, son
un bug. La causa: odds-api.io publica el total de goles como varias líneas
separadas por punto (más de/menos de 0.5, 1.5, 2.5, 3.5... goles), pero para
algunas de esas líneas el campo `point` no venía en la respuesta. Sin ese
dato, dos líneas completamente distintas (ej. "menos de 0.5 goles", muy poco
probable, y "menos de 8.5 goles", casi segura) colapsaban al mismo nombre
interno ("under") y el sistema terminaba comparando la cuota de una línea
contra la probabilidad justa de otra — de ahí el EV disparatado.

Se corrigió en dos capas:

1. **`odds_provider.py` ya no acepta una línea de `totals`/`spreads` sin
   `point`** — la descarta y deja un WARNING en el log en vez de arriesgar
   ese cruce. Esto evita el bug de raíz, pero no confirma que el resto del
   parseo de esas líneas (nombres, formatos) sea 100% correcto — solo se
   verificó a fondo contra la documentación pública el mercado 1X2 (ver el
   comentario al inicio de `odds_provider.py`).
2. **`value_detection.allowed_markets` queda en `["h2h"]` por defecto** —
   como red de seguridad adicional, totals/spreads quedan deshabilitados
   hasta que alguien confirme, revisando el log de varias corridas reales,
   que esas líneas ya no se están descartando por falta de `point` y que los
   EV que producen son razonables (unos pocos %, no cientos).
3. **`value_detection.max_ev_pct` (50% por defecto)** — un tope general,
   independiente del punto anterior: cualquier EV por encima de eso se
   descarta con un WARNING en vez de mostrarse como pick, sea cual sea la
   causa. Ningún value bet real y bien calculado se acerca a eso.

Si quieres habilitar totals/spreads más adelante: agrégalos a
`allowed_markets`, corre el workflow manualmente un par de días, y revisa el
log buscando la palabra "descarta" — si no aparece nada y los EV de esos
mercados se ven razonables, es una señal razonable de que el parseo está
funcionando bien para tu cobertura de ligas.

### "Las 10 mejores", no partidos al azar

La selección diaria (`select_daily_picks` en `daily.py`) siempre ordena
**todos** los candidatos por EV% descendente y toma los 10 primeros
(diversificando por partido cuando alcanza). Nunca hay una selección
aleatoria en ningún punto del pipeline. Con cobertura mundial va a haber,
cualquier día, muchos más partidos candidatos que con solo Colombia — así que
el "top 10" es una selección real entre un universo grande, no casi todo lo
disponible.

## Cómo funciona el ciclo diario

Cada mañana, un solo job (`valuebet.daily_job`) hace esto en orden:

1. **Liquida ayer**: para cada pick que se envió el día anterior, consulta el
   marcador final del partido y determina automáticamente si ganó, perdió o
   empujó (push) — sin que tengas que confirmarlo tú. Esto es el desempeño
   *del modelo*, independiente de si realmente apostaste o no cada pick (para
   lo segundo existe la CLI, ver más abajo). Envía el resumen por Telegram +
   una imagen (`output/latest_results.png`).
2. **Si hoy es día 1 del mes**: cierra el mes anterior con un resumen aparte —
   ver la sección "Resumen mensual" más abajo.
3. **Cuotas de hoy, mundial**: lista los partidos de fútbol de todas las
   ligas para la ventana del día (`daily.lookahead_days`) y consulta cuotas
   públicas de Betplay (ver `odds_provider.target_bookmakers` — Wplay no está
   cubierta por el proveedor actual, ver "Alcance" más arriba) y de un libro
   de referencia (por defecto Bet365 — no Pinnacle, ver "Sobre el libro de
   referencia" más arriba) vía [odds-api.io](https://odds-api.io) (agregador
   de solo lectura — no inicia sesión en ninguna casa). Para no gastar una consulta
   de API por partido — con cobertura mundial puede haber cientos por día —
   se piden en lotes de 10 (`GET /odds/multi`), acotados por
   `daily.max_events_per_run` (400 por defecto) para no agotar tu cuota del
   plan gratuito de odds-api.io; si algún día se alcanza el tope, el log lo
   avisa explícitamente en vez de fallar en silencio.
4. **Devig + EV**: le quita el margen a las cuotas del libro de referencia
   (Bet365 por defecto — ver "Sobre el libro de referencia" más arriba)
   (`multiplicative` o `shin`) para estimar la probabilidad justa de cada
   resultado, y calcula el EV% de la cuota que ofrece Betplay.
5. **Top 10 del día**: selecciona los 10 picks con mayor EV, diversificando
   por partido (máx. 1 selección por partido salvo que no alcancen picks
   distintos). Los guarda en la base de datos y envía el mensaje + la imagen
   (`output/latest_picks.png`) por Telegram.
6. **Cola de Instagram**: si Instagram está configurado, convierte a JPEG cada
   imagen generada en la corrida (picks/resultados/resumen mensual) y las dos
   deja anotadas en `output/instagram_queue.json`. La publicación real ocurre
   en un paso posterior del workflow, después del commit/push — ver "Identidad
   de marca e Instagram" más abajo para el porqué y el detalle completo.

Todo esto corre una vez al día vía GitHub Actions — no necesitas un servidor
prendido 24/7, y no hace falta un segundo workflow para lo mensual ni para
Instagram: la misma corrida de las 7 a.m. hace todo.

## Resumen mensual

El día 1 de cada mes, `daily_job.py` además genera y envía un resumen del mes
calendario que acaba de terminar (`valuebet/monthly.py`):

- **Total de picks**, **ganados**, **perdidos**, **anulados** y **tasa de
  acierto** del mes.
- **Si el mes fue rentable o no**, con un banner grande verde/rojo
  ("MES RENTABLE" / "MES NO RENTABLE").

Para poder declarar "rentable" hace falta asumir algún stake, y el sistema no
tiene registrado cuánto apostaste realmente en cada pick (eso solo existe si
usaste la CLI para confirmar cada apuesta con su monto real). Por eso el
resumen mensual usa el supuesto estándar de **stake plano de 1 unidad por
pick**: si ganó, suma `(cuota - 1)` unidades; si perdió, resta 1 unidad; un
push no suma ni resta. Es el mismo criterio que usan la mayoría de tipsters
para reportar desempeño sin depender del dinero real de cada seguidor — mide
si el *modelo* fue rentable, no necesariamente tu banca. Esto se explica en
el propio mensaje de Telegram y en el pie de la imagen, para que quede claro
que no es tu PnL real (para eso está `valuebet.cli stats`, ver más abajo).

Se guarda como `output/latest_monthly_summary.png` y se envía por Telegram.
Si un mes no tuvo ningún pick registrado, simplemente se omite (no se manda
un resumen vacío).

### Cómo leer un pick

Los mensajes de Telegram, la imagen y la CLI ya no muestran los códigos
internos (`h2h`, `home`, `over_2.5`) sino una descripción en español armada
con los nombres reales de los equipos (`src/valuebet/descriptions.py`). Por
ejemplo:

| Lo que verás                          | Qué significa                                                                 |
|----------------------------------------|--------------------------------------------------------------------------------|
| `Gana Millonarios (Local)`             | Apuesta de resultado (1X2) a que gana el equipo que juega de local.           |
| `Gana Nacional (Visitante)`            | A que gana el equipo que juega de visitante.                                  |
| `Empate`                               | A que el partido termina empatado.                                            |
| `Más de 2.5 goles en el partido`       | Mercado de "total de goles" (over/under): a que entre ambos equipos anotan más de 2.5 goles (es decir, 3 o más). |
| `Menos de 2.5 goles en el partido`     | Lo contrario: 2 goles o menos entre ambos equipos.                            |
| `Millonarios con hándicap -1.5`        | Hándicap asiático: para que la apuesta gane, Millonarios debe ganar por 2 goles de diferencia o más (se le "resta" 1.5 al marcador). |

El `@2.20` que sigue es la cuota decimal ofrecida (lo que multiplica tu stake
si ganas — @2.20 significa que $10.000 apostados devuelven $22.000 en total
si aciertas) y el nombre entre paréntesis es la casa (por ahora, Betplay —
ver "Alcance") donde está esa cuota.

## Despliegue 100% gratuito: GitHub Actions (no un servidor de Google Cloud)

Tenías dos opciones disponibles — un servidor gratuito de Google Cloud, o
GitHub Actions — y elegí **GitHub Actions** por esto:

- **Cero mantenimiento de servidor**: no hay que actualizar el SO, configurar
  systemd/cron, abrir puertos, ni preocuparte de que la VM se caiga o de que
  Google cambie las condiciones del free tier (limitado a regiones
  específicas y a una sola instancia `e2-micro`).
- **Gratis de verdad para esta carga**: el job corre ~1-2 minutos una vez al
  día. El tier gratuito de GitHub Actions da 2.000 minutos/mes en repos
  privados (e ilimitado en públicos) — este uso ni se nota.
- **Los secretos se gestionan solos**: GitHub Secrets ya resuelve guardar tu
  API key y token de Telegram de forma segura, sin tener que configurar IAM
  ni Secret Manager de GCP.
- **El propio repo es la base de datos**: la corrida se limita a leer el
  repo, correr el script y commitear de vuelta `data/valuebet.db` y las
  imágenes generadas. No hace falta una base de datos externa (Cloud SQL,
  Firestore, etc.).

Si en el futuro quieres alertas *intradía* (no solo el resumen de las 7 a.m.)
o cualquier cosa que necesite un proceso corriendo continuamente, ahí sí
tendría sentido usar Cloud Run + Cloud Scheduler (ambos con tier gratuito) —
pero para un resumen diario, es complejidad que no se necesita.

### Configurar el despliegue

1. Crea un repo en tu cuenta de GitHub y sube este proyecto (`git push`). Si
   vas a activar Instagram, el repo tiene que ser **público** — ver el porqué
   en "Identidad de marca e Instagram".
2. En el repo: **Settings → Secrets and variables → Actions → New repository
   secret**, y crea:
   - `ODDS_API_KEY` — tu API key de https://odds-api.io
   - `TELEGRAM_BOT_TOKEN` — el token que te dio @BotFather
   - `TELEGRAM_CHAT_ID` — tu chat_id (ver instrucciones más abajo)
   - `IG_ACCESS_TOKEN` y `IG_USER_ID` — **opcionales**, solo si quieres
     publicación automática en Instagram (ver esa sección para cómo
     obtenerlos). Si los dejas sin crear, todo lo demás sigue funcionando
     normal — Instagram simplemente queda desactivado.
3. Revisa `config.example.yaml` (bankroll, `min_ev_pct`, `num_picks`, ligas)
   y ajústalo a tu gusto — **no** pongas ahí tus claves reales, esas van solo
   en los Secrets del repo. `config.yaml` está en `.gitignore` y el workflow
   lo genera automáticamente a partir de `config.example.yaml` en cada corrida.
4. En la pestaña **Actions** del repo, verifica que el workflow "Resumen
   diario de BotBet (fútbol)" aparezca habilitado.
5. Pruébalo manualmente: **Actions → Resumen diario de BotBet (fútbol) →
   Run workflow**. Revisa los logs y que te llegue el mensaje de Telegram.
6. Listo — de ahí en adelante corre solo todos los días a las 7:00 a.m. hora
   de Bogotá (`cron: "0 12 * * *"`, UTC fijo — Colombia no tiene horario de
   verano).

El workflow (`.github/workflows/daily.yml`) hace: checkout → instala
dependencias → corre `valuebet.daily_job` → **commitea y pushea** `data/` y
`output/` de vuelta al repo → **publica en Instagram** (si está configurado,
leyendo `output/instagram_queue.json` que dejó el paso anterior). Las
imágenes se sobreescriben cada día (`latest_picks.png`, `latest_results.png`,
sus versiones `.jpg` para Instagram) para no inflar el repo con cientos de
PNGs — Telegram e Instagram ya quedan como el archivo histórico de cada
imagen publicada. Si prefieres guardar un PNG por fecha en el repo, es un
cambio pequeño en `daily_job.py`.

## Identidad de marca e Instagram

### El nombre y el logo

**BotBet** combina **Bot** (todo corre solo, sin intervención manual) con
**Bet** (apuestas) — pensado para que se lea de un vistazo qué hace la cuenta
e invite a seguirla para ver los pronósticos del día. El logo
(`src/valuebet/branding.py`) es una insignia con una "B" y dos motivos
pequeños que explican el nombre: tres nodos conectados arriba a la derecha
(el "Bot") y un trazo ascendente tipo gráfico de cuota abajo (el "Bet"). Se
dibuja 100% con Pillow — sin archivos de imagen externos — así que cambiar
colores o proporciones es editar ese único archivo. La cabecera de las tres
piezas de redes sociales (picks, resultados, resumen mensual) usa la misma
insignia junto al nombre y un tagline corto ("PRONÓSTICOS · FÚTBOL MUNDIAL").

Para la foto de perfil de Instagram, genera un PNG cuadrado de alta
resolución con:

```bash
PYTHONPATH=src python3 -c "from valuebet.branding import render_profile_icon; render_profile_icon('botbet_profile.png', size=1024)"
```

Súbela **a mano** una sola vez desde la app o instagram.com — la API de
publicación de contenido de Instagram no permite cambiar la foto de perfil.

### Cómo funciona la publicación automática

Instagram exige dos cosas que este proyecto tiene que resolver sin pagar
hosting: que la imagen sea **JPEG**, y que esté en una **URL pública**
accesible desde internet. La solución, sin servidor propio:

1. `daily_job.py`/`monthly.py` convierten cada imagen generada a JPEG y
   encolan una entrada (tipo, ruta, caption) en memoria durante la corrida.
2. Al terminar, se vuelca esa cola a `output/instagram_queue.json`, que el
   workflow commitea y pushea al repo junto con los `.jpg` — igual que ya
   hacía con la base de datos y los PNG.
3. **Recién después del push**, un paso aparte del workflow corre
   `scripts/publish_instagram.py`: lee el manifiesto, arma la URL pública de
   cada imagen como `raw.githubusercontent.com/<repo>/<sha-del-commit>/<ruta>`
   (usa el SHA exacto que se acaba de pushear, no la rama, para no depender de
   tiempos de propagación) y llama a la Graph API de Instagram en dos pasos:
   crea el "media container" con esa URL, y lo publica.

Esto **requiere que el repositorio sea público** — `raw.githubusercontent.com`
solo sirve contenido de repos públicos. Si prefieres mantenerlo privado,
Instagram simplemente se queda desactivado (no afecta a Telegram ni al resto
del bot); la alternativa sería subir las imágenes a algún storage público
gratuito con URL propia (ej. un bucket con acceso público) en vez de usar el
repo, cambiando solo `scripts/publish_instagram.py`.

### Prerrequisitos en Meta (cuenta de Instagram)

Publicar por API en Instagram no es instantáneo — hay que preparar la cuenta
primero, y esto toma tiempo real, no solo minutos:

1. La cuenta de Instagram debe ser **Business o Creator** (no personal), y
   estar **vinculada a una Página de Facebook**.
2. Crea una app en [developers.facebook.com](https://developers.facebook.com),
   agrégale el producto "Instagram" y conecta la cuenta.
3. Genera un **token de acceso de larga duración** con los permisos
   `instagram_business_content_publish` (o `instagram_content_publish`,
   según la versión de la API) y `pages_read_engagement`. Ese token va en el
   secreto `IG_ACCESS_TOKEN`; el ID numérico de la cuenta de Instagram
   (`GET /me/accounts` → la página → su cuenta de IG vinculada) va en
   `IG_USER_ID`.
4. **App Review**: mientras la app esté en modo de desarrollo, solo puede
   publicar en cuentas de prueba que tú mismo agregues como tester. Para
   publicar en la cuenta real de BotBet, Meta exige pasar **revisión de la
   app** (App Review), donde tienes que explicar el caso de uso y demostrar
   que manejas los datos correctamente. Esto puede tardar de días a semanas —
   planéalo con anticipación y no asumas que quedará listo el mismo día que
   lo pidas.
5. Límite de publicaciones: la cuenta tiene un tope de publicaciones por API
   en 24 horas (consultable en `GET /<IG_ID>/content_publishing_limit`); con
   máximo 3 publicaciones al día (picks + resultados + resumen mensual, y
   este último solo una vez al mes) este proyecto está muy por debajo.

### Una nota honesta sobre contenido de apuestas en Meta

Meta tiene políticas específicas para contenido relacionado con apuestas/juego
de azar, y el escrutinio puede ser mayor que para una cuenta genérica —
incluso siendo contenido puramente informativo y sin apuestas reales
involucradas. Por eso los captions que genera el bot (`daily_job.py`,
`monthly.py`) son deliberadamente informativos, no promocionales: dejan claro
que es "análisis estadístico automatizado, no una recomendación de apuesta ni
garantía de resultado", e incluyen la etiqueta +18. Aun así, no hay garantía
de que la app pase App Review a la primera o de que la cuenta no reciba
restricciones de alcance — es un riesgo real a tener en cuenta, no solo un
trámite. Si App Review es rechazado o tarda demasiado, el resto del bot
(Telegram, histórico, resumen mensual) sigue funcionando exactamente igual;
Instagram es una capa adicional, no una dependencia del resto del sistema.

## Instalación local (para probar antes de desplegar, u operar la CLI)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp config.example.yaml config.yaml
# edita config.yaml con tu banca, umbrales, ligas, etc.
# las claves (api_key/bot_token/chat_id) puedes ponerlas en config.yaml para
# uso local, o exportarlas como variables de entorno (ODDS_API_KEY,
# TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID) — estas últimas siempre ganan.
```

### Obtener una API key de cuotas

1. Regístrate en https://odds-api.io (tiene tier gratuito).
2. Copia tu API key en `odds_provider.api_key` en `config.yaml` (o expórtala
   como `ODDS_API_KEY`).
3. Antes de correr en serio, verifica con tu propia key en
   `GET https://api.odds-api.io/v3/bookmakers?apiKey=TU_KEY` — no solo en su
   sitio de marketing, que no siempre coincide — los nombres exactos que
   usan para Betplay y tu libro de referencia elegido (por defecto Bet365;
   ni Wplay ni Pinnacle están cubiertas por este proveedor — ver "Alcance" y
   "Sobre el libro de referencia" más arriba). Confirma también que dejar
   `leagues: []`
   efectivamente te trae partidos de todas las ligas de fútbol (no solo las
   principales) — este proyecto está construido contra su documentación
   pública de agosto 2026, pero las
   APIs de terceros cambian. Si algo no calza, ajusta `_MARKET_NAME_MAP` en
   `src/valuebet/odds_provider.py`. Lo mismo aplica a `get_event_result`
   (usada para la liquidación automática) — confírmalo con una llamada real
   antes de confiar el histórico a ojos cerrados.

### Crear el bot de Telegram

1. Habla con `@BotFather` en Telegram → `/newbot` → te da un `bot_token`.
2. Envíale cualquier mensaje a tu bot recién creado.
3. Visita `https://api.telegram.org/bot<TU_TOKEN>/getUpdates` y copia el
   `chat.id` que aparece — ese es tu `chat_id`.
4. Completa ambos en `config.yaml` o en los Secrets del repo.

## Uso

```bash
# Job diario completo (esto es lo que corre GitHub Actions cada mañana):
# liquida los picks pendientes de días anteriores + genera los 10 de hoy.
PYTHONPATH=src python3 -m valuebet.daily_job --config config.yaml

# Ver los picks de una fecha / el histórico en la base de datos:
PYTHONPATH=src python3 -c "
from valuebet.storage.db import Storage
s = Storage('data/valuebet.db')
for r in s.list_picks_for_date('2026-08-22'):
    print(dict(r))
"

# --- Opcional / avanzado: motor continuo de escaneo (no es necesario para el
# resumen diario; útil solo si además quieres alertas en tiempo real durante
# el día, corriendo en tu propia máquina o servidor) ---
PYTHONPATH=src python3 -m valuebet.main --config config.yaml --once
PYTHONPATH=src python3 -m valuebet.main --config config.yaml   # loop continuo

# --- CLI para llevar tu contabilidad REAL (lo que tú de verdad apostaste,
# distinto del histórico automático de picks) ---
PYTHONPATH=src python3 -m valuebet.cli list-pending
PYTHONPATH=src python3 -m valuebet.cli confirm 12 --stake 20000
PYTHONPATH=src python3 -m valuebet.cli reject 12
PYTHONPATH=src python3 -m valuebet.cli settle 12 --result won --pnl 18000
PYTHONPATH=src python3 -m valuebet.cli stats
```

## Pruebas

```bash
PYTHONPATH=src python3 -m pytest tests/ -v
```

102 tests cubren: devig (multiplicative y Shin), cálculo de EV, criterio de
Kelly con sus topes, detección de value bets con eventos simulados (sin red),
selección diversificada de los top-N picks diarios, liquidación automática
para 1x2/totales/hándicap con marcadores simulados, las descripciones legibles
en español para cada tipo de mercado, generación de las imágenes (dimensiones
y que no truene con 0 picks, incluida la del resumen mensual), el cálculo de
rentabilidad mensual con stake plano (incluyendo el caso "cruza de diciembre a
enero" y el caso "mes sin picks"), que el resumen mensual solo dispare el día
1, sobreescritura de secretos por variable de entorno (incluidos los de
Instagram), migración de una base de datos con el esquema anterior, el ciclo
de vida completo en SQLite, el logo/ícono de marca, la conversión a JPEG y el
manifiesto de la cola de Instagram, el cliente de la API de Instagram
(mockeado, sin red real), el manejo de errores HTTP del proveedor de cuotas
(no reintentar 4xx, sí reintentar 429/5xx, loguear el cuerpo de la respuesta),
el filtrado por `allowed_markets` y el tope `max_ev_pct` (incluyendo una
reproducción directa del bug real de EV disparatado por líneas de totals sin
`point`), el descarte de líneas de totals/spreads sin `point` en el parseo,
y una prueba de integración end-to-end del job diario con un proveedor falso
— con y sin Instagram configurado.

## Gestión de riesgo y apuesta responsable

- `kelly_fraction` (25% por defecto, usado en el motor continuo opcional):
  nunca uses Kelly completo, es muy agresivo y asume que tu probabilidad
  estimada es exacta, cosa que no lo es.
- `max_stake_pct` / `daily_stake_limit_pct` / `daily_loss_limit_pct`: topes
  duros de banca — si se alcanza el límite de pérdida diaria, se pausan las
  nuevas sugerencias y se te avisa.
- El resumen diario de 10 picks es informativo — **no** implica que debas
  apostar los 10. Sigue aplicando tu propio criterio y gestión de banca al
  decidir cuáles tomar y con qué monto.

Aun con un modelo de EV bien calibrado, la varianza a corto plazo es alta:
rachas de pérdidas de decenas de apuestas seguidas ocurren incluso con
ventaja real. El histórico automático de `daily_picks` existe justamente
para que puedas ver el desempeño real del modelo a lo largo de semanas/meses
antes de confiar en él con dinero — no lo tomes en serio con solo unos días
de datos. Este bot es una herramienta de análisis, no una promesa de
ganancias. Si en algún momento notas que apostar deja de sentirse como una
actividad controlada, la Línea de Prevención de Ludopatía en Colombia
(consulta el canal vigente de Coljuegos/Ministerio de Salud) es un buen
punto de partida.

## Extender el proyecto

- **Otro proveedor de cuotas**: implementa la interfaz `OddsProvider` en
  `src/valuebet/odds_provider.py` (incluyendo `get_event_result` para la
  liquidación) y regístralo en `build_provider`.
- **Más mercados**: ajusta `_MARKET_NAME_MAP` y prueba con `totals`/`spreads`
  reales de tu proveedor — el parseo de líneas con `point` ya está soportado
  de forma genérica en `_parse_odds_line`, `settlement.py` ya sabe liquidar
  esos dos tipos además de `h2h`, y `descriptions.py` ya sabe redactarlos en
  español. Un mercado nuevo (ej. córners, tarjetas) necesita las tres piezas.
- **Guardar un PNG por fecha** en vez de sobreescribir `latest_*.png`: cambia
  las rutas de salida en `daily_job.py` a algo como
  `f"{cfg.output_dir}/{pick_date_str}_picks.png"`.
- **Closing Line Value (CLV)**: `value_bets`/`daily_picks` en SQLite ya
  guardan cuota y fecha; se puede añadir un job que, al cerrar el mercado,
  compare tu cuota tomada contra la cuota de cierre del libro de referencia
  para medir qué tan buena fue tu selección independientemente del resultado.
- **Cambiar el nombre/logo de marca**: todo vive en `src/valuebet/branding.py`
  (`BRAND_NAME`, `BRAND_TAGLINE`, `render_icon`) — no hay assets externos que
  regenerar a mano. Ojo: el nombre también aparece "quemado" en los hashtags
  de los captions (`_picks_caption`/`_results_caption` en `daily_job.py`,
  `_monthly_caption` en `monthly.py`) y en el título del workflow.
- **Otra red social**: `social_publish.py` ya separa "generar+encolar" de
  "publicar" — para otra plataforma (ej. X/Twitter) alcanza con un cliente
  nuevo tipo `instagram.py` y otro paso en el workflow después del push, sin
  tocar cómo se generan las imágenes.
