# 📡 Directiva: Arbitrage Scanner v5.5 (SaaS Tier)

## Propósito
Detectar ineficiencias mediante un **Modelo de Coherencia Probabilística (Bayesian)** `P(General) ≤ P(Nominación)` en mercados políticos de Polymarket, mitigando el riesgo direccional oculto, gestionando ejecuciones parciales (Partial Fills/IOC), dynamic slippage y salidas mean-revertidas (Velocity).

## Pipeline Completo (13 módulos)

### 1. Matching (Robusto)
- Normalización de nombres: minúsculas, sin puntuación, sin variantes de "Jr."
- Múltiples patrones regex por tipo (nominación vs elección general)
- Blacklist de candidatos meme hardcoded

### 2. Discovery (Dinámico)
- Re-descubre pares cada 1 hora  
- Filtro de volumen combinado mínimo: `$500,000`
- Solo acepta candidatos con AMBOS mercados (nominación + elección general)

### 3. Filtro de Coherencia Bayesiana Normalizado
El **Conditional Pricing Model** postula que el Fair Value Real para `P(Gen)` no es `P(Nom)`, sino:
`Fair_P(Gen) = P(Nom) * P(Party_Win)`.
- El bot evalúa en caliente la suma total de `P(Gen)` del bloque del mismo partido. 
- Para evitar que la suma excedentaria (ruido de mercado) eleve `P(Party_Win) > 1.0`, esta se normaliza estáticamente: `prob_party_win = min(1.0, sum(...))`
- Sólo evaluamos `delta` de arbitraje si hay inconsistencias profundas contra este valor teórico ponderado.

### 4. Overexposure Layer (Límites por Partido)
- Para evadir overexposure sectario por correlación invisible, se trackea: `party_exposure[Partido]`.
- Se deniega acumular más exposición estructural para el lado si un partido (como el `Democratic` o `Republican`) supera un predefinido `MAX_PARTY_EXPOSURE = 600`.

### 4. Probabilidad Real (micro-VWAP Adaptativo)
- NO usar mid-price (falso con spreads grandes)
- Sample size = `min($5, liquidez × 2%)` — escala con profundidad real
- Evita distorsión en mercados con poca liquidez

### 5. Sizing Dinámico por Convicción y Portfolio
- Base inicial: Liquidez mínima cruzada (`min_liq`)
- Exposición Global Limitada: `MAX_TOTAL_EXPOSURE = 1000` (el tracking limita la entrada)
- Sizing Convexo: `position = position_base * (edge_score ** 1.5) * confidence`
- Un edge deficiente o de baja confianza fuerza posiciones miniatura o nulas, reduciendo el drawdown latente, mientras que los prime edges explotan.
- Clamp: `$10 - $150`

### 6. Ejecución Liquidity-First y Drift Simlation (Worst-Case Escapes)
Para esquivar el "optimismo algorítmico", el sistema ejecuta simulaciones avanzadas:
1. **Impact Penalty (Drift):** La rentabilidad penaliza un sub-VWAP con `buy_res["vwap"] *= (1 + impact_score)` simulando bots rivales apilando órdenes enfrente de nosotros.
2. **Paso Preventivo (Worst-Case)**: Simula que TODA la liquidez inmediata se fuga (`skip_levels=1` y `skip_levels=2`). Si es negativo, **BLOQUEA**.
3. **Liquidity-First Execution:** Las órdenes no van "Buy Nom -> Sell Gen", sino de la pata *menos líquida* a la más líquida. Si falla la más difícil, abortamos rápido protegiendo capital. Activa **Hedge Fallback** solo en caso fatal asimétrico usando órdenes limitadas al precio de recompra conservador (`* 0.95` o `* 1.05`), garantizando salir sin un dump ciego al bottom del book.
Gestión **Partial Fills / Secuencialidad (IOC)**: Las inyecciones en el CLOB viajan con el payload `"orderType": "IOC"`. En el evento de partial fills asimétricos, abortamos el riesgo lanzando una compensación direccional a mercado mediante nuestro Hedge Limitado.

### 7. Edge con Slippage Convexo
```
gross = revenue_sell - cost_buy
fees  = cost_buy × 1% + revenue_sell × 1%   (2 trades = 2 fees)
impact_convexo = (size / liq) ** 1.3
slip  = cost_buy × dynamic_slip             (dynamic = base + k * impact_convexo + level_penalty)
net   = gross - fees - slip
```
Mínimo aceptable: `net_edge ≥ 2%`

### 8. Confidence Score (Penta-Factor)
Filtro normalizado de oportunidades balanceando red y mercado:
```
liquidity_score = min(min_liq / 10000, 1)
latency_penalty = max(0, 1 - latency)
confidence = (edge_score * 0.4) + (delta_score * 0.2) + (spread_score * 0.2) + (liquidity_score * 0.1) + (latency_penalty * 0.1)
```
Si `confidence < Threshold`, se descarta o minimiza el tamaño drásticamente.

### 9. Confirmación Autónoma de Latencia (Adaptive)
- Edge debe ser ≥ 2% en X scans consecutivos.
- `CONFIRM_SCANS = 1` si `confidence > 0.70` (aprox. 30 seg, ataque ágil).
- `CONFIRM_SCANS = 2` para convicciones estándar.
- Latencia Red máxima tolerada reducida a HFT level: `0.35s` (350ms).

### 10. OpportunityTracker (ENTRADA + SALIDA)
- `open()`: registra primera oportunidad confirmada → Telegram ENTRADA
- `update()`: actualiza peak_edge, scan_count
- `should_exit()`: retorna (True, razón) si:
  - `net_edge < 0` (mercado corrigió)  
  - `velocity < -0.005` sostenido temporalmente (**Mean Reversion temprano**)
  - Holding > 72 horas → señal de salida forzada
- `close()`: elimina y retorna datos para Telegram SALIDA

### 11. Cooldown Anti-Spam
- 6 horas entre alertas ENTRADA por candidato
- Las señales de SALIDA siempre se emiten (sin cooldown)

## Honestidad sobre Limitaciones
El impacto de mercado real solo puede modelarse con datos de microestructura en tiempo real. El sistema usa una aproximación conservadora doble:
1. **Degradación de segunda pata** (skip 1 nivel del book)
2. **Haircut fijo 1.5%** sobre el edge calculado

Esto sobreestima costes pero nunca los subestima. Preferible en arbitraje.

## Ejecución
```powershell
python -X utf8 src/arbitrage_bot.py
```

## Outputs
- `artifacts/arbitrage_log.json` — Log de todas las señales (ENTRY, UPDATE, EXIT)
- Telegram → Alertas de entrada y salida con PnL estimado

## Bitácora de Anomalías
| Fecha | Versión | Error | Solución |
|---|---|---|---|
| 2026-04-13 | v1.0 | Top-of-book, sin fees, sin cooldown, regex frágil, discovery estático | v2.0 |
| 2026-04-13 | v2.0 | Unidades mezcladas USDC/shares en sell, fee % del edge, mid-price, size fijo, sin meme filter, sin confirmación temporal | v3.0 |
| 2026-04-13 | v3.0 | Sin impacto de mercado, micro-VWAP distorsionado en libros finos | v4.0 |
| 2026-04-13 | v4.0 | Correlación sin filtro bayesiano provocaba riesgo direccional oculto. Fallos de sell tras buy (partial fills). Slippage fijo sobrestimaba/subestimaba. | v5.0 (Bayesian Coherence, Slippage Dinámico, Confidence Score, Salida por Momentum) |
| 2026-04-13 | v5.1 | Ejecución secuencial sin fallback real (causaba direccionalidad en fail). Confidence score sin base matemática sólida (no normalizado). Latencia no vigilada. Exit momentum causaba falsos positivos por ruido de 1 scan. | v5.2 (Sizing Proporcional, Queue Penalty CLOB, Worst-case precheck) |
| 2026-04-13 | v5.2 | Liquidity Drift ignorado, Secuencial estática (Riesgo en Gen Thin books). Limitante latencia suave (1s). Confidence linear dejaba dinero en mesa. | v5.3 (Liquidity-First Exec., Impact Penalty, Convex Sizing, Latency 0.7s, Adaptive Scans, Limit Max Exposure) |
| 2026-04-14 | v5.3 | Slippage lineal, Latencia en CLOB aún letal (>300ms), Edge naive no predecía condicionabilidad del partido en Nominación -> General. Fills podían colgarse sin IOC. | v5.4 ("Quant Tier": Conditional Prob Modeling, Slippage Convexo (`impact**1.3`), Max Party Correlated Exp., Latency 350ms, IOC Orders) |
| 2026-04-14 | v5.4 | Arquitectura de red acoplada ralentizando el scanner (Latencia irreal), fallos al volcar dump (market order 0.01 se colgaba en libros finos), SaaS Tracking Analytics limitados. | v5.5 ("SaaS Tier": Separación Caching/Escáner, Hedges con Límites Inteligentes (`0.95/1.05`), Normalización de P(Partidos), Discovery en 5 min, Analíticas de Slippage). |
| 2026-04-14 | v5.5 | TypeError: can't subtract offset-naive and offset-aware datetimes en discovery periódico al restar con `datetime.min` | Solución: Instanciar global states como offset-aware usando `datetime.min.replace(tzinfo=timezone.utc)` |

## Restricciones
- Obligatorio: Al crear variables globales de `datetime`, deben ser siempre offset-aware (`timezone.utc`). No mezclar `datetime.min` o `datetime.now()` (naive) con offset-aware.
