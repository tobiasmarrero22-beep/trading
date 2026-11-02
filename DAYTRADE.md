# Bot de trading diario (cripto)

Opera BTC/USDT (configurable) varias veces por día con velas de 15 minutos, **solo comprando al contado**:
sin apalancamiento ni ventas en corto, así que nunca podés perder más de lo que pusiste en el bot.

> **Leé esto primero.** La mayoría de la gente que hace trading diario pierde plata. Con 200 USD, ganar
> 9 USD por día significa un 4,5 % diario, algo que ni los mejores fondos logran. El objetivo de este bot
> es que **midas con datos reales** cuánto gana o pierde de verdad, después de comisiones, sin arriesgar
> nada hasta que vos lo decidas.

## Cómo decide

Dos estrategias (se elige en `daytrade.yaml`):

- **`reversion`**: compra cuando el precio está en tendencia alcista (por encima de su media de 200
  velas) pero tuvo una caída fuerte de corto plazo (RSI por debajo de 30). Vende cuando rebota
  (RSI por encima de 55).
- **`breakout`**: compra cuando el precio, en tendencia alcista, supera el máximo de las últimas 20 velas.
  Vende si pierde la tendencia.

Todas las operaciones tienen:

- un **stop-loss** a 1,5 × ATR (la volatilidad reciente) por debajo de la compra;
- una **toma de ganancia** a 2,5 × ATR por encima;
- un **tiempo máximo** de 8 horas: si para entonces no tocó ninguno de los dos, vende igual.

## Gestión del riesgo

| Regla | Valor por defecto |
|---|---|
| Riesgo máximo por operación | 1 % de la cuenta (2 USD con 200 USD) |
| Pérdida máxima por día | 3 %: después no abre más operaciones hasta el día siguiente (UTC) |
| Operaciones por día | 6 como máximo |
| Objetivo diario | opcional (`daily_profit_target_usd: 9`): al llegar, deja de operar ese día |
| Capital del bot | `initial_usd`: aunque tu cuenta tenga más, el bot no usa más que eso |
| Interruptor | si existe el archivo `STOP`, no abre operaciones nuevas (sí cierra la que esté abierta) |

## Paso 1: backtest con datos reales

```bash
pip install -r requirements.txt
python daytrade.py backtest              # último año, compara las dos estrategias
python daytrade.py backtest --days 90    # últimos 90 días
python daytrade.py backtest --trades     # además guarda cada operación en un CSV
```

Fijate sobre todo en estos números:

- **`ganancia_promedio_por_dia_usd`**;
- **`factor_de_beneficio`**: tiene que ser mayor que 1. Si es menor, la estrategia pierde plata;
- **`comisiones_usd`**: suele ser lo que mata al trading diario;
- **`comprar_y_mantener_%`**: si comprar BTC y no tocarlo rindió más, el bot no está aportando nada.

Podés cambiar los parámetros de `daytrade.yaml` y repetir el backtest. Cuidado: si ajustás tanto los
parámetros que el pasado da perfecto, casi seguro va a fallar en el futuro. Una forma de comprobarlo es
afinar con `--days 365` y después mirar si sigue funcionando en otro período.

## Paso 2: práctica con precios reales (sin cuenta)

`mode: paper_local` (viene así por defecto). Usa los precios reales de Binance y simula las órdenes
con comisión y deslizamiento. No necesita claves.

```bash
python daytrade.py run       # queda corriendo; Ctrl+C para frenarlo
python daytrade.py report    # ganancias y pérdidas por día
```

Dejalo correr **al menos 2 a 4 semanas** antes de sacar conclusiones. El estado se guarda en `state/`,
así que si lo frenás y lo volvés a arrancar, sigue donde estaba.

## Paso 3: testnet de Binance (órdenes de verdad, plata ficticia)

Sirve para comprobar que las órdenes funcionan con el exchange real.

1. Entrá a [testnet.binance.vision](https://testnet.binance.vision), iniciá sesión con GitHub y generá
   una clave HMAC.
2. Copiá `.env.example` a `.env` y pegá `BINANCE_API_KEY` y `BINANCE_API_SECRET`.
3. En `daytrade.yaml`, poné `mode: binance_testnet`.
4. Corré `python daytrade.py check` para confirmar la conexión y después `python daytrade.py run`.

Los precios de la testnet a veces no se parecen a los reales (tiene poco volumen), así que para medir
la rentabilidad sirve más el paso 2.

## Paso 4: dinero real (solo si los pasos 2 y 3 te convencieron)

1. Binance funciona en Argentina: podés comprar USDT con pesos (P2P o depósito). Pasá al saldo
   *Spot* solo lo que estés dispuesto a perder.
2. En binance.com, creá una clave de API con permisos de **lectura y trading al contado únicamente**:
   **nunca actives los retiros**. Si podés, restringila a la IP de tu compu.
3. En `.env`, poné las claves reales y `DAYTRADE_LIVE_CONFIRM=SI`.
4. En `daytrade.yaml`, poné `mode: binance_live` e `initial_usd` con lo que querés que use el bot.
5. Corré `python daytrade.py check`: tiene que decir "Retiros deshabilitados". Recién ahí, `python daytrade.py run`.

## Importante: el bot tiene que estar prendido

El stop-loss lo controla el bot cada 30 segundos, no queda puesto en Binance. **Si apagás la compu
con una operación abierta, esa operación queda sin protección** hasta que lo vuelvas a arrancar. Hay
dos opciones: dejar la compu prendida o usar un servidor barato en la nube (un VPS de unos 5 USD por
mes). Con 200 USD de capital, ese costo ya se come buena parte de la ganancia esperada, algo más para
tener en cuenta.

## Impuestos

Las ganancias con cripto tienen tratamiento impositivo propio en Argentina. Consultalo con un contador.
