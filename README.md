# BotBet (valuebet-bot)

**BotBet** — el nombre juega con **Bot** (todo el pipeline corre solo) + **Bet**
(apuestas) — es un bot de **análisis** de apuestas deportivas de valor (value
betting) de fútbol de un conjunto curado de ligas y torneos (Primera A
colombiana, Argentina, Brasil, MLS, Liga MX, las 5 grandes ligas europeas,
Champions/Europa/Conference League y Libertadores/Sudamericana — ver
"Alcance" más abajo), evaluando las cuotas que ofrece **Betplay** contra **Bet365** como
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

## Alcance: ligas de fútbol curadas + otros deportes opcionales (ej. NBA)

`odds_provider.sports` es una lista — por defecto `["football"]`. Hasta
agosto 2026 el filtro de ligas de fútbol quedaba vacío (`leagues: []`, "todas
las ligas de fútbol para las que el proveedor tenga datos"), pero eso incluía
cientos de ligas de segunda/tercera división, reservas y categorías
infantiles con cuotas poco fiables o mercados muy delgados. Ahora fútbol usa
un filtro curado por decisión explícita del usuario del proyecto: Colombia,
Argentina, Brasil, USA (solo MLS), México, las 5 grandes ligas europeas
(Premier League, LaLiga, Serie A, Bundesliga, Ligue 1), y los torneos de
clubes UEFA (Champions/Europa/Conference League) y CONMEBOL (Libertadores/
Sudamericana). El filtro completo, con los 15 slugs exactos y comentados uno
por uno, vive en `leagues_by_sport.football` en `config.example.yaml`.

Agregar otro deporte es solo una línea más en `sports` (los slugs válidos
están en `GET /sports` — con tu key:
`https://api.odds-api.io/v3/sports?apiKey=TU_KEY`) — el resto del pipeline
(parseo de cuotas 1X2, EV, liquidación) ya es genérico por deporte, no asume
fútbol.

El filtro de **ligas** funciona así: `leagues` es una lista global que se
aplica a cualquier deporte en `sports` que NO tenga su propia entrada en
`leagues_by_sport`. Como fútbol y basketball necesitan listas totalmente
distintas entre sí, ambos tienen su propia entrada y `leagues` queda en `[]`
sin usarse. Ejemplo (resumido) ya incluido en `config.example.yaml`:

```yaml
sports: ["football", "basketball"]
leagues: []                          # sin efecto: fútbol y basketball ya tienen
                                      # su propia entrada en leagues_by_sport
leagues_by_sport:
  football:                          # ver la lista completa (15 slugs,
    - "colombia-liga-dimayor-finalizacion"   # comentada uno por uno) en
    - "argentina-primera-lpf-clausura"       # config.example.yaml
    # ...
  basketball: ["usa-nba"]            # basketball: SOLO la NBA (si no, trae
                                      # también NCAA, WNBA, ligas menores...)
```

Si en algún momento quieres volver a cobertura mundial de fútbol (todas las
ligas, sin curar), basta con borrar la entrada `football:` de
`leagues_by_sport` (o dejarla en `[]`) — el mecanismo ya soporta los dos
modos, no hace falta tocar código.

### ⚠️ Dos riesgos de mantenimiento del filtro de fútbol

Los 15 slugs se verificaron uno por uno contra una respuesta real de
`GET /leagues?sport=football` (agosto 2026) — nunca se adivinaron, siguiendo
la misma disciplina que ya evitó repetir los bugs de Wplay, Pinnacle y el
campo `hdp` de este proyecto. Aun así, quedaron dos riesgos que esa única
respuesta no permitió descartar:

1. **Argentina y México juegan en dos mitades de temporada (Apertura /
   Clausura), y la API parece nombrar el slug de la liga por mitad**, no por
   temporada completa: en el snapshot, Argentina solo tenía slug para
   "Clausura" (`argentina-primera-lpf-clausura`) y México solo para
   "Apertura" (`mexico-liga-mx-apertura`) — no apareció un slug "genérico"
   sin mitad. Si es así, cuando cada torneo cierre y arranque la otra mitad
   del año, el slug probablemente cambie de nombre y el filtro deje de traer
   partidos de ese país **en silencio** (sin error, solo sin picks). Colombia
   tiene el mismo formato de temporada partida, pero ahí sí se pudo confirmar
   con certeza cuál de los dos slugs visibles es la Primera A real
   (`colombia-liga-dimayor-finalizacion` — "Liga DIMAYOR" es la nomenclatura
   oficial de Primera A, "Torneo DIMAYOR" es la Primera B; ver el comentario
   en `config.example.yaml`), pero probablemente tenga el mismo riesgo de
   cambio de slug al pasar de Finalización a Apertura.
2. **Los torneos UEFA y CONMEBOL solo mostraban un slug por fase actual**, no
   uno estable para todo el torneo: en el snapshot, las 3 copas UEFA
   aparecían únicamente como `...-playoff-round`, y Libertadores/Sudamericana
   como `...-knockout-stage` — no había, por ejemplo, un slug de "fase de
   grupos/liga" visible para comparar (probablemente porque esa fase no
   estaba activa en ese momento del calendario).

**Mitigación mientras tanto**: si de un día para otro dejan de aparecer picks
de Argentina, México, Colombia, o de algún torneo UEFA/CONMEBOL durante
varios días seguidos (y sí hay partidos reales de por medio), es la primera
señal de que el slug cambió. Vuelve a pedir
`GET https://api.odds-api.io/v3/leagues?sport=football&apiKey=TU_KEY`, busca
la liga/torneo afectado y actualiza el slug en `leagues_by_sport.football`.
No hay forma de resolver esto de una vez y para siempre sin más información
de la API (por ejemplo, si odds-api.io hiciera matching por prefijo en vez de
exacto, o publicara un slug estable por torneo) — este proyecto no adivina
esa respuesta, la deja documentada como riesgo abierto.

Antes de activar un deporte nuevo, verifica igual que con las casas de
apuestas (ver más abajo) que tanto tu casa objetivo como tu libro de
referencia efectivamente tengan cuotas para ese deporte/liga en odds-api.io —
que el deporte exista en general no garantiza que una casa puntual lo cubra.
Ten en cuenta también la temporada: la NBA juega de octubre a junio, así que
en pleno verano boreal es normal no recibir picks de basketball aunque todo
esté bien configurado.

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

### Mercados soportados: 1X2 (h2h), totales ("más/menos goles") y ambos anotan (btts)

Otro problema real descubierto en producción, más serio que los dos
anteriores: los primeros picks reales que llegaron traían **EV de +300% a
+760%** en mercados de "Total de goles" — cifras que no son valor real, son
un bug. La causa raíz original que se identificó: odds-api.io publica el
total de goles como varias líneas separadas por punto (más de/menos de 0.5,
1.5, 2.5, 3.5... goles), y el código buscaba ese valor en un campo `point`.
Sin ese dato, dos líneas completamente distintas (ej. "menos de 0.5 goles",
muy poco probable, y "menos de 8.5 goles", casi segura) colapsaban al mismo
nombre interno ("under") y el sistema terminaba comparando la cuota de una
línea contra la probabilidad justa de otra — de ahí el EV disparatado.

La primera corrección (descartar la línea si no traía `point`) frenó el
síntoma, pero dejó totals/spreads deshabilitados por precaución. Más
adelante se encontró la causa raíz exacta: **el campo nunca se llamó
`point`** — la documentación oficial de odds-api.io
(`docs.odds-api.io/api-reference/openapi.json`) confirma que el valor de la
línea viaja en un campo llamado **`hdp`**, tanto para `totals` como para
`spreads`. El código nunca lo había verificado contra la respuesta real (la
documentación pública solo mostraba un ejemplo completo del mercado 1X2), así
que la condición "sin `point`" era **siempre** verdadera — toda línea de
totals se descartaba, no solo las incompletas. Esto también reveló otro
problema menor: el nombre real del mercado de hándicap es `"Spread"`
(singular), que sin un alias explícito se normalizaba a `"spread"` en vez de
`"spreads"` (el valor usado en el resto del código) y quedaba fuera de
`allowed_markets` sin ningún aviso.

Con el campo correcto (`odds_provider.py` ahora lee `hdp`, con `point` como
alias de respaldo), la protección contra el cruce de líneas de gol distintas
sigue exactamente igual — solo que ahora las líneas SÍ tienen su valor de
línea real, en vez de descartarse siempre. Las tres capas de seguridad
originales se mantienen:

1. **`odds_provider.py` descarta cualquier línea de `totals`/`spreads` sin
   `hdp` (ni `point` de respaldo)** — WARNING en el log, nunca arriesga el
   cruce.
2. **`value_detection.allowed_markets`**: ahora incluye `"totals"` y
   `"btts"` por defecto (`["h2h", "totals", "btts"]`) — `"spreads"`
   (hándicap) no se activó por defecto por no haberse pedido explícitamente,
   pero el mismo arreglo aplica y se puede sumar.
3. **`value_detection.max_ev_pct` (50% por defecto)** — tope general
   independiente de lo anterior: cualquier EV por encima se descarta con
   WARNING sea cual sea la causa.
4. **`value_detection.max_totals_point` (5.5 goles por defecto)** — descarta
   líneas de `totals` con un punto por encima de este valor (ej. "más de 8.5
   goles"). Se agregó tras un caso real: dos picks de la primera ronda de la
   DFB-Pokal (Copa de Alemania) sugerían "más de 8.5" y "más de 7.5 goles"
   con EV positivo y bien calculado — sin cruce de líneas, el punto sí
   coincidía entre Betplay y el libro de referencia. El problema no es un
   bug: es que hasta un libro "sharp" como Bet365 dedica menos cuidado a
   líneas tan extremas/poco apostadas que a la línea principal (2.5), así
   que confiar en esa cuota como "justa" es más riesgoso, aunque el partido
   en cuestión (un club amateur contra uno de Bundesliga) sí pueda terminar
   en goleada. Pon `max_totals_point: null` en `config.yaml` si prefieres
   ver esas líneas de todos modos.
5. **`value_detection.min_odds` / `max_odds` (1.40 – 3.00 por defecto)** —
   ver la sección "Rango de cuota" justo abajo: generaliza el mismo problema
   de `max_totals_point` a cualquier mercado, no solo `totals`.

### Rango de cuota: por qué "mayor EV" no es lo mismo que "mejor pick"

El motivo real detrás de `max_totals_point` (punto 4 arriba) aplica en
general, no solo a líneas de goles: **el EV calculado es tan bueno como la
probabilidad "justa" que lo alimenta, y esa probabilidad es menos confiable
en resultados poco probables** — no importa si es "más de 7.5 goles" en
totals o un underdog aplastado en h2h. Un ejemplo numérico: si el modelo
(la cuota devigada de Bet365) dice que un resultado tiene 3% de probabilidad
real y la casa lo ofrece a cuota 45, el EV sale en +35% — pero si la
probabilidad real fuera 2% en vez de 3% (un error de apenas 1 punto
porcentual, nada raro en las colas de una distribución), el EV real es
-10%. Ordenar los candidatos `ORDER BY ev_pct DESC` sin ningún otro filtro
selecciona sistemáticamente estos casos — son justo los que más EV
*aparente* muestran, precisamente porque el error de estimación se
magnifica más ahí.

`value_detection.min_odds` (1.40 por defecto) y `max_odds` (3.00 por
defecto) descartan cualquier pick con cuota ofrecida (`target_bookmakers`)
fuera de ese rango, en cualquier mercado habilitado — antes de que llegue
al ranking por EV. No elimina el riesgo de estimación (`min_odds`/`max_odds`
no reemplazan a un modelo bien calibrado), pero acota el sistema a la zona
donde un error de calibración pesa proporcionalmente menos. El rango
1.40–3.00 es el que el propio usuario del proyecto definió como punto de
partida razonable, no un óptimo demostrado — si el volumen diario de picks
queda muy bajo con las ligas curadas (ver "Alcance" más arriba) puede tener
sentido ampliarlo (ej. 1.30–5.00). Pon `min_odds: null` y/o `max_odds: null`
en `config.yaml` para desactivar cada tope por separado.

**Lo que este filtro NO resuelve**: la propuesta original también incluía
comparar la probabilidad del modelo contra un consenso de varias casas sin
margen — eso ahora mismo no es viable con el plan gratuito de odds-api.io,
que permite solo 2 bookmakers propios en total (target + reference); Betplay
y Bet365 ya ocupan ambos cupos, así que no hay margen para sumar un tercer
libro de referencia sin pagar. El **CLV** (ver la sección de más abajo) sigue
siendo la validación más fuerte disponible sin ese consenso: compara la
cuota tomada contra la cuota de cierre, y con suficiente muestra (cientos de
picks) es evidencia de si el sistema encuentra valor real o solo está
encontrando falsos EV en las colas de la distribución.

De todos modos, vale la pena revisar el log las primeras corridas después de
activar un mercado nuevo, buscando la palabra "descarta" — si aparece muy
seguido para tu cobertura de ligas, puede indicar que algún bookmaker manda
el campo con otro nombre distinto a `hdp`/`point` que todavía no se cubrió.

**Ambos anotan (btts / "Both Teams To Score")**: a diferencia de
totals/spreads, no es una línea con un valor numérico (no lleva `hdp`) sino
una proposición fija con dos resultados, "sí" o "no" — el usuario confirmó
con una consulta real a `GET /markets?sport=football` que odds-api.io sí lo
ofrece para fútbol (`shape: "yesno"`), así que se agregó siguiendo la misma
disciplina de las otras correcciones: nunca adivinar el nombre de un campo o
mercado, verificarlo contra una respuesta real primero. La liquidación
automática (`settlement.py`) marca "sí" como ganador cuando ambos equipos
anotaron al menos un gol (según el marcador final) y "no" en cualquier otro
caso.

### "Las 10 mejores", no partidos al azar

La selección diaria (`select_daily_picks` en `daily.py`) siempre ordena
**todos** los candidatos por EV% descendente y toma los 10 primeros
(diversificando por partido cuando alcanza). Nunca hay una selección
aleatoria en ningún punto del pipeline. Con las ligas curadas (ver "Alcance"
más arriba) va a haber, cualquier día, muchos más partidos candidatos que con
solo Colombia — así que el "top 10" es una selección real entre un universo
grande, no casi todo lo disponible.

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
3. **Cuotas de hoy, ligas curadas**: lista los partidos de fútbol de las
   ligas configuradas (ver "Alcance" más arriba) para la ventana del día
   (`daily.lookahead_days`) y consulta cuotas públicas de Betplay (ver
   `odds_provider.target_bookmakers` — Wplay no está cubierta por el
   proveedor actual, ver "Alcance" más arriba) y de un libro de referencia
   (por defecto Bet365 — no Pinnacle, ver "Sobre el libro de referencia" más
   arriba) vía [odds-api.io](https://odds-api.io) (agregador de solo lectura
   — no inicia sesión en ninguna casa). Para no gastar una consulta de API
   por partido — puede haber cientos por día entre todas las ligas — se
   piden en lotes de 10 (`GET /odds/multi`), acotados por
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

## Closing Line Value (CLV): validar si hay valor real, sin esperar meses

El acierto/fallo de picks individuales no dice mucho por sí solo — con EV de
un dígito, hace falta *literalmente miles* de apuestas para que la tasa de
acierto real se distinga del ruido estadístico. **CLV** es la métrica que
usan los apostadores profesionales para resolver este problema: en vez de
mirar si un pick ganó, compara la cuota que tomaste contra la cuota que ese
mismo mercado tenía justo antes de que arrancara el partido (la "cuota de
cierre"). Si constantemente consigues mejor precio que el cierre, es
evidencia sólida de que estás encontrando valor real — y se puede leer
pick por pick, no hace falta esperar una muestra enorme.

```
clv_pct = (cuota_tomada / cuota_de_cierre - 1) * 100
```

Positivo = conseguiste una cuota más alta (mejor) que la que hubo al cierre,
el mercado se movió después a tu favor. Negativo = lo contrario.

**Por qué hay un workflow aparte para esto (`clv_snapshot.yml`)**: odds-api.io
no da cuotas históricas en el plan gratuito (`GET /historical/closing-lines`
es de pago) — no se puede pedir "¿qué cuota tenía este partido justo antes de
arrancar?" después de que ya pasó. La única forma de conseguir el dato gratis
es capturarlo en vivo, mientras el mercado sigue abierto. Por eso
`clv_snapshot.yml` corre cada 3 horas (en el minuto 30, para no chocar con el
commit del resumen diario) y busca picks pendientes cuyo partido arranca
dentro de `daily.clv_window_hours` (3 horas por defecto) — si encuentra
alguno, pide la cuota actual de ese mismo bookmaker/mercado/selección y la
guarda como aproximación al cierre (`src/valuebet/clv.py`). No todos los
picks van a tener cierre capturado (si la casa cierra el mercado antes de que
corra esta captura, por ejemplo) — no es un error, simplemente ese pick queda
sin dato de CLV.

El resumen mensual muestra el **CLV promedio** y cuántos picks tuvieron
cierre capturado, tanto en el mensaje/imagen de Telegram como en el caption
de Instagram — pero solo aparece una vez que haya al menos un pick con cierre
capturado en el mes; con muestras muy chicas (unos pocos picks) es apenas una
señal preliminar, no una conclusión.

El mensaje diario de "resultados de ayer" también muestra el CLV de **cada
pick individual** (cuando se alcanzó a capturar su cierre), y una línea de
**últimos 30 días** con aciertos y CLV promedio en ventana móvil — a
diferencia del resumen mensual, esta no se resetea el día 1 de cada mes, así
que da una lectura útil sin importar qué día estés revisando.

### Enlace directo a la casa de apuestas (opcional)

Si tu flujo es ver el pick por Telegram y entrar a apostar a mano, podés
configurar `odds_provider.bookmaker_links` para que cada pick traiga un link
de un tap a tu casa:

```yaml
odds_provider:
  bookmaker_links:
    Betplay: "https://tu-url-verificada.../"
```

Viene **vacío por defecto a propósito**. Al buscar la URL oficial de Betplay
para documentar este ejemplo, ningún resultado de la primera página de
Google era el dominio real — todos eran sitios "clon" o de afiliados que
imitan el nombre de la casa. Nunca pegues acá el primer link que te aparezca
buscando: copiá la URL directamente de tu sesión ya iniciada en la app o el
sitio real de tu casa de apuestas.

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

## Señales secundarias: PlayerElo + lesiones (api-football) — EN CONSTRUCCIÓN

Además del libro de referencia (Bet365), el proyecto puede consultar dos
fuentes externas más, pero solo para los ~10 picks que YA pasaron el filtro
de EV — nunca para los cientos de partidos candidatos de cada corrida — para
caber en sus planes gratuitos:

- **PlayerElo** (`playerelo.football`): un rating Elo por jugador (no por
  equipo), que arma su propia probabilidad de partido a partir de la
  alineación esperada, cubriendo 176 competiciones. La idea es usarlo como
  **segunda opinión independiente**: comparar la probabilidad "justa" de
  Bet365 contra la de PlayerElo antes de mostrar un pick — si coinciden, más
  confianza; si no, se puede señalar. Esto ataca directamente el problema
  que ya vimos con ligas menores: si la cuota de Bet365 en un partido oscuro
  es poco confiable, un segundo modelo independiente (que no depende de
  cuánto dinero mueve el mercado en esa liga) sirve de chequeo cruzado.
  Plan gratuito: 500 solicitudes/mes, 10/minuto.
- **API-Football** (`api-football.com`): datos de lesiones/bajas por
  equipo. La idea es agregar una línea informativa al mensaje de Telegram
  cuando haya bajas reportadas en los equipos de un pick — **sin tocar el
  cálculo de EV**, solo como contexto extra para tu decisión manual (a
  diferencia de un ajuste automático del pick por noticias, que se descartó
  antes por riesgoso — ver la sección de CLV arriba). Plan gratuito: 100
  solicitudes/día.

  **⚠️ Riesgo real encontrado en el primer diagnóstico (2026-08-25)**: `GET
  /injuries?team=<id>&season=2026` respondió `errors.plan`: "Free plans do
  not have access to this season, try from 2022 to 2024." — el plan
  gratuito **no cubre la temporada en curso** para `/injuries`, que es
  justo lo que esta señal necesitaría (bajas antes del partido de HOY). Se
  está probando si `/injuries?fixture=<id>` (por partido específico) o
  `/injuries?date=<hoy>` esquivan la restricción — ver
  `scripts/verify_api_football.py`. Si ninguna funciona, esta señal
  concreta (lesiones) no es viable en el plan gratuito y quedaría
  descartada o pendiente de una futura suscripción de pago, sin afectar a
  PlayerElo (señal independiente, sin este problema).

**Por qué "en construcción" y no activado por defecto**: este proyecto ya se
tropezó tres veces (Wplay, Pinnacle, el campo `hdp` de odds-api.io) por
asumir la forma de una respuesta externa sin verificarla primero. Para no
repetirlo con dos APIs nuevas a la vez, `src/valuebet/playerelo_provider.py`
e `injuries_provider.py` solo traen la conectividad (autenticación,
reintentos) — el parseo de campos específicos (probabilidades, nombres de
equipo, lesiones) se escribe en una segunda entrega, después de correr los
scripts de diagnóstico y pegarle la salida real (el JSON crudo) a Claude.

**Cómo correr los scripts sin instalar nada en tu PC**: como el resto de
este proyecto, no hace falta Python local — hay un workflow de GitHub
Actions dedicado, `verify_secondary_signals.yml`, que solo corre manualmente
(nunca solo, no tiene `schedule`, así que no gasta tu cuota de API sin que
tú lo dispares):

1. **Settings → Secrets and variables → Actions** en tu repo → agrega
   `PLAYERELO_API_KEY` y `APIFOOTBALL_API_KEY` con tus keys reales (gratis,
   ver los links de arriba).
2. Pestaña **Actions** → "Diagnóstico PlayerElo + API-Football" → **Run
   workflow** (mismo botón que ya usas para correr `daily.yml` a mano). Si
   te suscribiste a API-Football vía RapidAPI en vez de directo, marca la
   opción `via_rapidapi` antes de correrlo.
3. Cuando termine, abre la corrida y copia todo el texto de los pasos
   "Diagnóstico PlayerElo" y "Diagnóstico API-Football" — pégaselo a Claude.

Si prefieres correrlo en tu PC en vez de Actions, también funciona
(requiere Python 3.11+ y `pip install -r requirements.txt` primero):

```
export PLAYERELO_API_KEY="tu-key-real"
python scripts/verify_playerelo.py

export APIFOOTBALL_API_KEY="tu-key-real"
python scripts/verify_api_football.py "Real Madrid"
```

`secondary_signals.*.enabled` queda en `false` en `config.example.yaml`
hasta que el parseo real esté escrito.

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
   diario de BotBet" aparezca habilitado.
5. Pruébalo manualmente: **Actions → Resumen diario de BotBet →
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
   "Sobre el libro de referencia" más arriba). Confirma también que los slugs
   de `leagues_by_sport.football` (ver "Alcance" más arriba) sigan siendo
   válidos con `GET /leagues?sport=football&apiKey=TU_KEY` — este proyecto
   está construido contra su documentación pública de agosto 2026, pero las
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
