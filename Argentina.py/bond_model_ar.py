"""
MOTOR DE CALCULO DE BONOS — Argentina (bond_model_ar.py)
=========================================================

Hermano del bond_model.py de Paraguay/Uruguay, pero generalizado para lo
que tienen de particular los bonos argentinos en USD:

- CUPON ESCALONADO (step-up): Bonares/Globales de la reestructuración 2020
  (AL29/AL30/AL35/AE38/AL41, GD29/GD30/GD35/GD38/GD41/GD46) no pagan una
  tasa fija única - la tasa de cupón sube en fechas predeterminadas (ej.
  0,125% los primeros dos años, después 0,50%, después 0,75%, etc.). Acá
  eso se modela con `coupon_schedule`: una lista de (fecha desde la que
  rige, tasa anual %) en vez de un unico `coupon_pct`. Un bono de cupón
  fijo (ej. AO27/AO28/AN29, o los Bopreal) simplemente tiene una lista de
  UNA sola entrada.
- PUT (opción del TENEDOR, no del emisor): los BOPREAL le dan al tenedor
  la posibilidad de pedirle al BCRA que se los recompre antes del
  vencimiento, a partir de determinada fecha. Es lo inverso de un CALL
  (que es opción del EMISOR, como en Paraguay/Uruguay): acá el que decide
  ejercer es quien tiene el bono. Por eso NO se calcula automaticamente
  un escenario "to worst" - la app deja elegir a mano si se quiere
  pricear a vencimiento o al put (ver `cashflows(put_date=..., ...)` acá
  y `selector_escenario()` en bonos_ar_app.py, que maneja el detalle de
  fechas/tipo de cada ventana de put).
- DOS CONVENCIONES DE DIAS DISTINTAS EN EL MISMO BONO: el INTERES CORRIDO
  (accrued) se calcula con 30/360 sobre el cronograma de cupones - la
  convencion que usan los prospectos (Decreto 676/2020, SEC 424B5,
  Comunicaciones BCRA). Pero el YIELD (la tasa que iguala el precio con
  el valor presente de los flujos) se resuelve como un XIRR clasico
  (Actual/365, capitalizacion anual efectiva): cada flujo se descuenta
  por la cantidad REAL de dias calendario entre el settlement y su
  fecha de pago, no por una cuenta de "periodos" de 30/360. Por eso la
  tasa nativa de este motor es la TEA (tasa efectiva anual) directamente
  - no una tasa nominal semestral como en bond_model.py de Paraguay/
  Uruguay. TNA Semianual (la tasa nominal anual, base semestral) se
  deriva de la TEA: TNA = ((1+TEA)^(180/360) - 1) * (360/180).

El resto (paridad, capital vigente) es conceptualmente igual que en
Paraguay/Uruguay - ver el docstring de bond_model.py para esas
definiciones si hace falta repasarlas.
"""

from dataclasses import dataclass, field
from datetime import date
import pandas as pd


# ---------------------------------------------------------------------------
# Funciones de fechas (convencion 30/360) — identicas a bond_model.py
# ---------------------------------------------------------------------------
def days_30_360(d1: date, d2: date) -> int:
    """Cuenta los dias entre d1 y d2 asumiendo meses de 30 dias (30/360 US).
    Es la convencion que usan TODOS los bonos argentinos acá modelados
    (Decreto 676/2020 Anexo III y SEC 424B5 lo dicen textualmente para
    Bonares/Globales; las Comunicaciones del BCRA lo dicen igual para
    BOPREAL; las resoluciones de AO27/AO28/AN29 tambien)."""
    y1, m1, day1 = d1.year, d1.month, d1.day
    y2, m2, day2 = d2.year, d2.month, d2.day
    if day1 == 31:
        day1 = 30
    if day2 == 31 and day1 == 30:
        day2 = 30
    return (y2 - y1) * 360 + (m2 - m1) * 30 + (day2 - day1)


def add_months(d: date, months: int) -> date:
    """Suma (o resta) meses a una fecha, ajustando al ultimo dia valido del
    mes de destino si hace falta (ej. 31 de abril -> 30 de abril)."""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                       31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


# ---------------------------------------------------------------------------
# Bond: el bono en si
# ---------------------------------------------------------------------------
@dataclass
class Bond:
    """Representa un bono argentino en USD: cupón fijo O escalonado,
    amortización opcional en cuotas. Los puts de BOPREAL (opción del
    tenedor de forzar la redención anticipada) NO viven en esta clase -
    `cashflows()`/`clean_price()`/etc. aceptan un `put_date`/
    `put_price_pct` puntual por llamada (un escenario a la vez, elegido
    a mano por la usuaria - ver docstring del módulo), pero el
    cronograma de QUÉ ventanas de put tiene cada bono y de qué tipo
    (BCRA/AFIP) es un detalle de UI que vive en bonos_ar_app.py
    (PUTS_VENTANAS/selector_escenario), no acá.

    Atributos:
        coupon_schedule: lista de (fecha_desde, tasa_anual_pct), ordenada
            ascendente por fecha. La tasa vigente en una fecha `d` es la de
            la ultima entrada con fecha_desde <= d (ver coupon_rate_at).
            Un bono de cupón fijo es, simplemente, una lista de UNA sola
            entrada (fecha_desde = fecha de emisión, tasa = la fija).
        maturity: fecha de vencimiento final.
        face: valor nominal / "cara" del bono, casi siempre 100.
        freq: pagos de cupon por año (2 = semestral, 12 = mensual como
            AO27/AO28).
        amortization: lista opcional de (fecha, fraccion) - fechas en las
            que se repaga una fraccion del capital ORIGINAL. Vacia si el
            bono es bullet (paga el 100% del capital de una vez, al
            vencimiento).
        coupon_anchor: fecha opcional que define el DIA regular del
            cronograma de cupones (mes/dia), cuando ese dia NO coincide con
            el de `maturity`. Pasa en AL29/GD29 (cupones el 9-ene/9-jul,
            pero el vencimiento final cae el 10-jul) y AE38/GD38 (cupones
            el 9-ene/9-jul, vencimiento final el 11-ene) - el vencimiento
            "real" a veces corre uno o dos dias respecto del patron regular
            de pagos. Si es None (el caso normal), se usa `maturity` como
            ancla de todo el cronograma, sin diferencia. Se ignora si
            `coupon_dates_explicit` esta cargado.
        coupon_dates_explicit: lista opcional de fechas de cupon EXACTAS
            (incluida la fecha de emision, como primer elemento, y el
            vencimiento, como ultimo), que reemplaza por completo el
            calculo via `coupon_anchor`/`add_months`. Pensado para bonos
            con cronograma "fin de mes ajustado a dia habil" (AO27/AO28/
            AO29) - en vez de aproximar esas fechas con add_months, se
            cargan tal cual las publica la resolucion de emision. Si es
            None (el caso normal), el cronograma se genera automaticamente
            (ver coupon_dates())."""

    coupon_schedule: list
    maturity: date
    face: float = 100.0
    freq: int = 2
    coupon_anchor: date = None
    coupon_dates_explicit: list = None
    amortization: list = field(default_factory=list)

    def coupon_rate_at(self, d: date) -> float:
        """Tasa de cupón anual (%) vigente en la fecha `d`, según el
        cronograma de step-up. Si `d` es anterior a la primera entrada del
        cronograma, se usa igual la primera tasa (para no romper el
        calculo del primer período de vida del bono)."""
        vigente = self.coupon_schedule[0][1]
        for fecha_desde, tasa in sorted(self.coupon_schedule):
            if fecha_desde <= d:
                vigente = tasa
            else:
                break
        return vigente

    def _outstanding_at(self, d: date) -> float:
        """Capital vigente (sobre el face original) despues de aplicar
        cualquier amortizacion con fecha <= d. Sin amortizacion, siempre
        es self.face (bono bullet: el capital nunca baja antes del
        vencimiento)."""
        if not self.amortization:
            return self.face
        pagado = sum(frac for (fecha, frac) in self.amortization if fecha <= d)
        return self.face * (1 - pagado)

    def coupon_dates(self, settlement: date) -> list:
        """Reconstruye el calendario de pagos de cupon. Si `coupon_dates_explicit`
        esta cargado, se usa tal cual (ver docstring de la clase) - solo se
        recorta a [cupon/emision anterior al settlement, ...cupones
        futuros..., vencimiento]. Si no, se retrocede desde el ancla del
        cronograma (`coupon_anchor`, o `maturity` si no hay uno distinto)
        en multiplos de `12/freq` meses, y se usa `maturity` como la fecha
        REAL del ultimo flujo.

        OJO 1: las fechas intermedias se calculan SIEMPRE a partir del
        ancla (nunca de la fecha anterior ya calculada) - importante para
        vencimientos en dia 31 (ej. BOPREAL, 31-oct/30-abr): si se
        encadenara add_months sobre la ultima fecha obtenida, el "30" de
        un mes corto (abril) se arrastraria para siempre y el resto de
        las fechas de octubre saldrian en 30 en vez de 31.

        OJO 2: cuando `coupon_anchor` difiere de `maturity` (AL29/GD29,
        AE38/GD38 - ver docstring de la clase), el ancla NO debe aparecer
        como una fecha de cupon en si misma (seria un cupon extra,
        redundante con el vencimiento real, a un dia o dos de distancia) -
        por eso el loop empieza en k=1 (un periodo ANTES del ancla), no en
        k=0."""
        if self.coupon_dates_explicit:
            todas = sorted(self.coupon_dates_explicit)
            anteriores = [d for d in todas if d <= settlement]
            futuras = [d for d in todas if d > settlement]
            prev_coupon = anteriores[-1] if anteriores else todas[0]
            return [prev_coupon] + futuras

        step = 12 // self.freq
        ancla = self.coupon_anchor or self.maturity
        dates = [self.maturity]
        k = 1
        while dates[-1] > settlement:
            dates.append(add_months(ancla, -step * k))
            k += 1
        dates.reverse()
        return dates

    def schedule(self, settlement: date):
        """Ubica al settlement dentro del calendario de cupones. Ver
        docstring de la version Paraguay/Uruguay (misma logica exacta)."""
        dates = self.coupon_dates(settlement)
        prev_coupon = dates[0]
        future = [d for d in dates[1:] if d > settlement]
        next_coupon = future[0]
        period_days = days_30_360(prev_coupon, next_coupon) or (360 // self.freq)
        accrued_days = days_30_360(prev_coupon, settlement)
        f = (period_days - accrued_days) / period_days
        return prev_coupon, next_coupon, future, period_days, accrued_days, f

    def cashflows(self, settlement: date, put_date: date = None, put_price_pct: float = None) -> pd.DataFrame:
        """Tabla de todos los pagos futuros del bono desde el settlement.

        Por defecto (put_date=None) asume que el bono llega hasta el
        vencimiento normal, amortizando en cuotas segun `amortization` (si
        las tiene) y devengando cupon segun la tasa vigente de
        `coupon_schedule` en cada periodo (step-up).

        Si se pasa `put_date` (una fecha en la que el TENEDOR decide
        ejercer el put a `put_price_pct`% del capital vigente en ese
        momento), los flujos se cortan ahi: cupones normales hasta esa
        fecha, y un flujo final de `outstanding_en_esa_fecha *
        put_price_pct / 100` en vez de seguir hasta el vencimiento. Si
        `put_date` no coincide con una fecha de cupon exacta, el ultimo
        flujo incluye el cupon corrido (30/360) hasta ese dia, sobre la
        tasa y el capital vigentes en ese momento.

        Los numeros salen SIN redondear (el redondeo es cosa de la
        interfaz, no del motor de calculo) - ver el mismo comentario en
        bond_model.py.

        Cada fila trae DOS medidas de "distancia" al flujo, para dos usos
        distintos: "periodos"/"dias_desde_settlement_30_360" (30/360, solo
        informativos/de referencia) y "dias_actual" (dias de calendario
        reales, settlement -> fecha del flujo) - este ultimo es el que usa
        dirty_price()/duration_convexity() para descontar (ver el XIRR en
        el docstring del modulo).
        """
        prev_coupon, _, future, _, _, f = self.schedule(settlement)
        period_nominal = 360 / self.freq

        outstanding = self._outstanding_at(prev_coupon)
        rows = []
        last_date, last_t = prev_coupon, f - 1
        for i, d in enumerate(future):
            t = f + i
            # El cupon de CADA periodo es proporcional a sus dias reales
            # (30/360) entre el flujo anterior y este - NO un monto fijo
            # tasa/freq. Para un periodo "regular" (ej. 30 dias en un
            # bono mensual, 180 en uno semestral) da exactamente lo mismo,
            # pero en bonos con fechas de fin de mes ajustadas a dia habil
            # (AO27/AO28/AO29) los periodos varian (29, 27, 35 dias...) y
            # el cupon tiene que variar con ellos.
            dias_periodo = days_30_360(last_date, d)
            rate = self.coupon_rate_at(last_date)
            coupon_amt = rate / 100 * outstanding * dias_periodo / 360

            if put_date is not None and put_date < d:
                # El put cae DENTRO de este periodo (antes del proximo
                # cupon regular): flujo final con cupon corrido (30/360)
                # desde el ultimo cupon pagado, sobre la tasa/capital de
                # ESTE periodo.
                stub_days = days_30_360(last_date, put_date)
                t_put = last_t + stub_days / period_nominal
                cupon_corrido = rate / 100 * outstanding * stub_days / 360
                monto_put = outstanding * put_price_pct / 100
                rows.append({
                    "fecha": put_date,
                    "dias_desde_settlement_30_360": days_30_360(settlement, put_date),
                    "dias_actual": (put_date - settlement).days,
                    "periodos": t_put,
                    "cupon": cupon_corrido,
                    "principal": monto_put,
                    "flujo_total": cupon_corrido + monto_put,
                })
                return pd.DataFrame(rows)

            if d == self.maturity:
                # En el vencimiento se paga TODO el capital que quede
                # vigente, sin importar lo que diga (o no diga)
                # `amortization` para esa fecha exacta - así un bono
                # bullet (sin ninguna fila en el cronograma) igual
                # devuelve el 100% del face al final, y de paso se evita
                # que un cronograma con amortizaciones que no suman
                # exactamente 1.0 (redondeo) deje un resto sin pagar.
                principal = outstanding
            else:
                amort_frac = next((frac for (fecha, frac) in self.amortization if fecha == d), 0.0)
                principal = self.face * amort_frac

            if put_date is not None and put_date == d:
                # El put coincide EXACTAMENTE con una fecha de cupon/amort:
                # se paga el cupon + amortizacion normal de ese dia, MAS el
                # capital remanente (si queda algo despues de esa
                # amortizacion) via el put.
                outstanding_post_amort = outstanding - principal
                monto_put = outstanding_post_amort * put_price_pct / 100
                rows.append({
                    "fecha": d,
                    "dias_desde_settlement_30_360": days_30_360(settlement, d),
                    "dias_actual": (d - settlement).days,
                    "periodos": t,
                    "cupon": coupon_amt,
                    "principal": principal + monto_put,
                    "flujo_total": coupon_amt + principal + monto_put,
                })
                return pd.DataFrame(rows)

            rows.append({
                "fecha": d,
                "dias_desde_settlement_30_360": days_30_360(settlement, d),
                "dias_actual": (d - settlement).days,
                "periodos": t,
                "cupon": coupon_amt,
                "principal": principal,
                "flujo_total": coupon_amt + principal,
            })
            outstanding -= principal
            last_date, last_t = d, t

        return pd.DataFrame(rows)

    def accrued_interest(self, settlement: date) -> float:
        """Interes corrido: la parte del cupon actual ya devengada pero
        todavia no pagada. Usa la tasa de cupon vigente en el inicio del
        periodo actual (soporta step-up) y el capital vigente en ese
        momento (soporta amortizacion). Se calcula directo por dias
        corridos (30/360) sobre 360 - NO tasa/freq*(dias/periodo) - para
        que de lo mismo sin importar si el periodo del bono es "regular"
        o no (ver el mismo comentario en cashflows())."""
        prev_coupon, _, _, _, accrued_days, _ = self.schedule(settlement)
        rate = self.coupon_rate_at(prev_coupon)
        outstanding = self._outstanding_at(prev_coupon)
        return rate / 100 * outstanding * accrued_days / 360

    def dirty_price(self, tea_pct: float, settlement: date, put_date: date = None,
                     put_price_pct: float = None) -> float:
        """Precio Dirty dado un yield: XIRR clasico - cada flujo se
        descuenta por su cantidad REAL de dias calendario hasta el
        settlement (Actual/365, capitalizacion anual efectiva), no por una
        cuenta de periodos 30/360 (ver docstring del modulo)."""
        cf = self.cashflows(settlement, put_date, put_price_pct)
        r = tea_pct / 100
        t_anios = cf["dias_actual"] / 365
        pv = cf["flujo_total"] / (1 + r) ** t_anios
        return float(pv.sum())

    def clean_price(self, tea_pct: float, settlement: date, put_date: date = None,
                     put_price_pct: float = None) -> float:
        """Precio Clean = precio Dirty menos el interes ya
        corrido (el interes corrido SI se calcula 30/360 - ver
        accrued_interest)."""
        return self.dirty_price(tea_pct, settlement, put_date, put_price_pct) - self.accrued_interest(settlement)

    def outstanding_pct(self, settlement: date) -> float:
        """Capital vigente en el settlement, en % del face ORIGINAL (100
        menos lo ya amortizado). Publico (a diferencia de _outstanding_at)
        porque la interfaz lo necesita para convertir entre precio "por 100
        de face original" (la unidad nativa de clean_price/dirty_price) y
        precio "por 100 de capital vigente" - la convencion de mercado para
        el precio CLEAN de un bono ya parcialmente amortizado (verificado
        contra una tabla de referencia real: el precio DIRTY se cotiza por
        100 de face original tal cual sale de este motor, pero el precio
        CLEAN se cotiza reescalado sobre el capital vigente, no sobre el
        original - dos convenciones distintas conviviendo en el mismo bono)."""
        prev_coupon, _, _, _, _, _ = self.schedule(settlement)
        return self._outstanding_at(prev_coupon)

    def paridad(self, clean_price: float, settlement: date) -> float:
        """Paridad = precio Dirty / valor tecnico (capital vigente + interes
        corrido), en %."""
        outstanding = self.outstanding_pct(settlement)
        accrued = self.accrued_interest(settlement)
        valor_tecnico = outstanding + accrued
        dirty = clean_price + accrued
        return dirty / valor_tecnico * 100

    def yield_from_clean_price(self, clean_price: float, settlement: date, tol: float = 1e-8,
                                max_iter: int = 100, put_date: date = None, put_price_pct: float = None) -> float:
        """Dado un precio Clean, encuentra la TEA que lo produce (para un
        escenario dado - vencimiento normal, o un put puntual) por
        busqueda binaria: el precio cae cuando la tasa sube (relacion
        monotona), asi que se acota el intervalo [lo, hi] hasta converger.

        El techo de 40.0 es solo el punto de partida, NO un limite real de
        TEA: un bono muy amortizado (ej. AL29, que ya devolvio 40% del
        capital) tiene una vida promedio mucho mas corta que su plazo a
        vencimiento, asi que a precios de descuento puede rendir bastante
        mas de 40% TEA sin que eso sea un error. Si el precio a `hi` sigue
        por encima del precio buscado (el yield real esta mas alla del
        techo), se duplica `hi` hasta lograr un bracket valido - antes,
        sin esto, la busqueda binaria convergia al techo (40.0) como si
        fuera la respuesta, subestimando silenciosamente el yield real.

        Los flujos (`cashflows`) y el interés corrido NO dependen de la
        TEA que se está probando - solo las fechas/montos cambian con el
        escenario (put_date/put_price_pct), que es fijo durante toda la
        búsqueda. Se calculan UNA sola vez antes de los loops en vez de
        recalcularlos (con su propio `schedule()`/`coupon_dates()`) en
        cada una de las ~150 iteraciones."""
        cf = self.cashflows(settlement, put_date, put_price_pct)
        accrued = self.accrued_interest(settlement)
        dias_actual = cf["dias_actual"]
        flujo_total = cf["flujo_total"]

        def clean_a(tea_pct: float) -> float:
            r = tea_pct / 100
            pv = flujo_total / (1 + r) ** (dias_actual / 365)
            return float(pv.sum()) - accrued

        lo, hi = -5.0, 40.0
        for _ in range(50):
            if clean_a(hi) <= clean_price:
                break
            hi *= 2
        for _ in range(max_iter):
            mid = (lo + hi) / 2
            if clean_a(mid) > clean_price:
                lo = mid
            else:
                hi = mid
            if abs(hi - lo) < tol:
                break
        return (lo + hi) / 2

    def duration_convexity(self, tea_pct: float, settlement: date, put_date: date = None,
                            put_price_pct: float = None):
        """Sensibilidad del precio ante cambios de yield, para el escenario
        elegido (vencimiento normal por defecto, o un put puntual). Con la
        TEA como tasa nativa (capitalizacion anual efectiva), duration
        modificada y convexidad ya no llevan la division por `freq` que
        tenian en la version con tasa nominal semestral - los tiempos
        `t` ya estan en años reales (dias_actual/365), no en "periodos"."""
        cf = self.cashflows(settlement, put_date, put_price_pct)
        r = tea_pct / 100
        t = cf["dias_actual"] / 365
        pv = cf["flujo_total"] / (1 + r) ** t
        dirty = pv.sum()
        macaulay_years = float((t * pv).sum() / dirty)
        modified_duration = macaulay_years / (1 + r)
        convexity = float((pv * t * (t + 1)).sum() / dirty) / (1 + r) ** 2
        return {
            "macaulay_years": macaulay_years,
            "modified_duration": modified_duration,
            "convexity": convexity,
        }

    def summary(self, settlement: date, clean_price: float = None, tea_pct: float = None,
                put_date: date = None, put_price_pct: float = None) -> dict:
        """Punto de entrada principal: le pasas precio O TEA (uno de los
        dos) para un escenario dado (vencimiento normal por defecto, o un
        put puntual si se pasan `put_date`/`put_price_pct`) y devuelve
        todo lo demas ya calculado y redondeado a 3 decimales.

        A diferencia de Paraguay/Uruguay (que calculan "to worst"
        automaticamente entre vencimiento y calls), acá el escenario lo
        elige la usuaria a mano - ver el docstring del modulo sobre por
        que los puts de BOPREAL no se resuelven solos."""
        if clean_price is None and tea_pct is None:
            raise ValueError("Pasa clean_price o tea_pct")

        if tea_pct is None:
            tea_pct = self.yield_from_clean_price(clean_price, settlement, put_date=put_date, put_price_pct=put_price_pct)
        if clean_price is None:
            clean_price = self.clean_price(tea_pct, settlement, put_date, put_price_pct)

        accrued = self.accrued_interest(settlement)
        dc = self.duration_convexity(tea_pct, settlement, put_date, put_price_pct)
        return {
            "settlement": settlement,
            "precio_clean": round(clean_price, 3),
            "precio_dirty": round(clean_price + accrued, 3),
            "interes_corrido": round(accrued, 3),
            "tea_pct": round(tea_pct, 3),
            "duracion_macaulay_anios": round(dc["macaulay_years"], 3),
            "duracion_modificada": round(dc["modified_duration"], 3),
            "convexidad": round(dc["convexity"], 3),
        }
