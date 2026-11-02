# Bots de inversión

Este repo tiene dos bots:

- **Inversión periódica** (este documento): invierte de 100 a 200 USD por mes en ETFs, a largo plazo.
- **Trading diario en cripto**: ver [DAYTRADE.md](DAYTRADE.md).

## Bot de inversión periódica

Bot que invierte por vos todos los meses (de 100 a 200 USD) en una cartera diversificada de ETFs,
la mantiene equilibrada y, si querés, se protege de las caídas grandes con un filtro de tendencia.

> **Aviso importante.** Esto es una herramienta, no un consejo financiero. Ningún bot garantiza ganancias
> y podés perder dinero. Empezá en modo práctica, mirá cómo se comporta varios meses y recién después
> decidí si usarlo con plata real.

## Qué hace

1. **Una vez por mes** toma el efectivo que depositaste y compra los ETFs de tu cartera (`config.yaml`).
2. **Rebalancea con los aportes:** el dinero nuevo va primero a lo que quedó por debajo de su peso objetivo.
   Solo vende si algo se desvió más de un 10 % (configurable). Así hay menos operaciones, menos comisiones
   y menos impuestos.
3. **Filtro de tendencia (opcional):** si un ETF cotiza por debajo de su media de 200 días, su parte pasa a
   `BIL` (letras del Tesoro de EE. UU., casi efectivo) hasta que vuelva a subir. Suele ganar algo menos en
   los años buenos, pero recorta mucho las caídas fuertes (2008, 2020, 2022).
4. **Controles de seguridad:** tope por orden, tope de órdenes por ejecución, pausa si la cartera cae más
   de un 5 % entre ejecuciones, interruptor de apagado (archivo `STOP`) y doble confirmación para operar
   con dinero real.

Cartera por defecto: 60 % `VT` (acciones de todo el mundo), 15 % `QQQ` (tecnológicas de EE. UU.)
y 25 % `IEF` (bonos del Tesoro de EE. UU.). Cambiala en `config.yaml`.

## Instalación

```bash
git clone https://github.com/tobiasmarrero22-beep/trading.git
cd trading
python -m venv .venv
source .venv/bin/activate        # en Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Paso 1: backtest (probar con datos históricos)

```bash
python main.py backtest
```

Baja precios reales desde 2007 y compara la estrategia **con** y **sin** filtro de tendencia, aportando
`monthly_usd` cada mes: cuánto aportaste, cuánto valdría hoy, rendimiento anual, peor caída y comisiones.
Probá cambiar la cartera, el aporte o la comisión (`backtest.commission_usd`) en `config.yaml`
y volvé a correrlo.

## Paso 2: práctica local (sin cuenta ni claves)

Con `mode: paper_local`, el bot simula una cuenta en `state/paper_account.json` y le "deposita"
`monthly_usd` cada mes.

```bash
python main.py run --dry-run   # muestra qué compraría hoy, sin hacer nada
python main.py run             # ejecuta la simulación
python main.py status          # muestra la cuenta simulada
```

Cada operación queda registrada en `logs/trades.csv`.

## Paso 3: práctica con Alpaca (precios reales, dinero ficticio)

1. Creá una cuenta en [alpaca.markets](https://alpaca.markets). La cuenta de **paper trading** es
   gratuita y solo pide un email.
2. En el panel de *Paper Trading*, generá las claves de la API.
3. `cp .env.example .env` y pegá las claves en `.env`.
4. En `config.yaml`, poné `mode: alpaca_paper`.
5. `python main.py run --dry-run` y después `python main.py run`.

En paper trading, Alpaca te da un saldo ficticio grande. El bot invierte todo el efectivo disponible
(con el tope `max_order_usd` por orden), así que, si querés simular de forma realista, reseteá la
cuenta de práctica con un saldo de, por ejemplo, 150 USD desde el panel de Alpaca.

## Paso 4: dinero real (solo cuando estés seguro)

1. Abrí y fondeá una cuenta real en Alpaca y generá las claves de la cuenta real.
2. En `config.yaml`, poné `mode: alpaca_live`.
3. En `.env`, poné `BOT_LIVE_CONFIRM=SI`. Sin eso, el bot se niega a operar.

### Notas para Argentina

- **Bróker:** verificá en el formulario de Alpaca si acepta cuentas reales de residentes argentinos (la
  lista de países cambia). Si no te acepta, **Interactive Brokers** sí suele aceptar residentes
  argentinos, tiene API y permite fracciones de acción, pero cobra una comisión por orden (alrededor de
  0,35 a 1 USD). En ese caso conviene invertir cada 2 o 3 meses en lugar de todos los meses, y hay que
  agregar al bot un conector para IBKR (todavía no está hecho). Otra opción es operar CEDEARs en pesos
  con un bróker local como InvertirOnline, que también tiene API (tampoco está hecho todavía).
- **Fondeo:** para invertir en un bróker del exterior necesitás mandar dólares desde una cuenta en USD.
  Consultá las comisiones de transferencia: con montos chicos pueden comerse buena parte del aporte.
- **Impuestos:** las inversiones en el exterior tienen tratamiento propio en Ganancias y Bienes Personales.
  Consultalo con un contador.

## Correrlo automáticamente

El bot está pensado para ejecutarse **todos los días hábiles**: solo opera una vez por mes (a partir de
`day_of_month`) y el resto de los días no hace nada. Si un día el mercado está cerrado, lo intenta al
día siguiente.

**Linux/Mac (cron).** `crontab -e` y agregá esta línea (de lunes a viernes a las 12:00, en el horario
de la máquina, con el mercado de EE. UU. abierto):

```
0 12 * * 1-5 cd /ruta/a/trading && .venv/bin/python main.py run >> logs/cron.log 2>&1
```

**Windows.** Usá el *Programador de tareas*, con una acción que ejecute
`C:\ruta\a\trading\.venv\Scripts\python.exe main.py run` y "Iniciar en" `C:\ruta\a\trading`.

## Apagado de emergencia

```bash
touch STOP     # el bot deja de operar
rm STOP        # lo reactiva
```

## Estructura

| Archivo | Qué hace |
|---|---|
| `config.yaml` | Toda la configuración: modo, cartera, aporte, límites |
| `bot/strategy.py` | Pesos objetivo, filtro de tendencia y cálculo de órdenes |
| `bot/backtest.py` | Simulación histórica con aportes mensuales y comisiones |
| `bot/risk.py` | Controles de seguridad |
| `bot/brokers.py` | Simulación local y conexión con Alpaca |
| `bot/runner.py` | Una ejecución completa (lo que corre cron) |
| `bot/data.py` | Precios (yfinance, con caché en `data/cache/`) |
| `tests/` | Tests (`python -m pytest`) |
