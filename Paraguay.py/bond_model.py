"""
MOTOR DE CALCULO DE BONOS (bond_model.py)
==========================================

Este archivo es el "cerebro" matematico de toda la app. No tiene nada de
Streamlit ni de interfaz: solo modela un bono soberano tipico (bullet,
cupon fijo, pago semestral, con opcion de calls/amortizacion anticipada)
y sabe convertir entre precio y yield, calcular cashflows, duration,
convexidad y paridad. La app (bonos_pyg_app.py) importa la clase Bond de
aca y solo se encarga de mostrar los numeros.

Conceptos clave para orientarse en el archivo:

- BONO "BULLET": paga cupones fijos periodicos y devuelve el 100% del
  capital (face) recien en la fecha de vencimiento (no amortiza antes).
- CALL (rescate anticipado): el emisor puede optar por devolver el
  capital antes del vencimiento normal, en una fecha y a un precio
  pactados de antemano. Un bono puede tener 0, 1 o varias fechas de call.
- AMORTIZACION: algunos bonos (ej. los "UI" de Uruguay) no devuelven el
  100% del capital de golpe al vencimiento - lo van pagando de a partes
  en fechas pactadas de antemano (normalmente los ultimos años de vida
  del bono). Cada pago de amortizacion reduce el capital vigente
  ("outstanding"), y los cupones siguientes se calculan sobre ESE
  capital ya reducido, no sobre el capital original.
- YIELD/PRECIO "TO WORST": cuando un bono tiene calls, no hay un unico
  "yield" - depende de si el emisor termina ejerciendo el call o no. La
  convencion de mercado es calcular TODOS los escenarios posibles
  (vencimiento normal + cada call) y quedarse con el PEOR para el
  tenedor (el yield mas bajo / el precio mas bajo). Si el bono no tiene
  calls, "to worst" es exactamente lo mismo que "to maturity" (YTM) - no
  cambia nada para los bonos bullet puros.
- CONVENCION 30/360: para contar dias entre dos fechas, se asume que todos
  los meses tienen 30 dias y el año 360. Es la convencion estandar en
  bonos soberanos emergentes en USD (no es la cantidad real de dias
  calendario, es una regla contable). Se probo tambien Actual/360, pero
  se descarto: al validar contra un numero de referencia real (precio a
  yield 9% para el Paraguay 31) 30/360 daba una coincidencia exacta a 6
  decimales (95.939241) mientras que Actual/360 daba 95.915 - evidencia
  bastante mas solida que la de la prueba anterior, asi que se volvio
  para atras.
- PRECIO LIMPIO (clean price) vs PRECIO SUCIO (dirty price): el precio
  limpio es el que se cotiza en pantalla/mercado. El precio sucio es lo
  que realmente se paga al comprar el bono, e incluye el interes ya
  devengado desde el ultimo pago de cupon (accrued interest):
      precio sucio = precio limpio + interes corrido
- DURATION: mide, en años, cuanto tarda en "recuperarse" el precio del bono
  pesando cada flujo por su valor presente (Macaulay), y cuanto se mueve el
  precio ante cambios de 1% en la tasa (duration modificada). A mayor
  duration, mas sensible el precio a cambios de yield.
- CONVEXIDAD: correccion de segundo orden a la duration (la relacion
  precio/yield no es una recta, es curva).
- PARIDAD: precio sucio dividido por el "valor tecnico" (capital + interes
  corrido), en %. Sirve para saber si el bono cotiza sobre o bajo su valor
  tecnico, mas alla del precio limpio nominal.
"""

from dataclasses import dataclass, field
from datetime import date
import pandas as pd


# ---------------------------------------------------------------------------
# Funciones de fechas (convencion 30/360)
# ---------------------------------------------------------------------------
def days_30_360(d1: date, d2: date) -> int:
    """Cuenta los dias entre d1 y d2 asumiendo meses de 30 dias (30/360 US).

    Ejemplo: de 15-ene a 15-feb son "30 dias" aunque el calendario real
    tenga 31. Esta es la convencion que usan los bonos que modelamos, asi
    que TODO el resto del archivo cuenta dias con esta funcion, nunca con
    resta de fechas directa.
    """
    y1, m1, day1 = d1.year, d1.month, d1.day
    y2, m2, day2 = d2.year, d2.month, d2.day
    if day1 == 31:
        day1 = 30
    if day2 == 31 and day1 == 30:
        day2 = 30
    return (y2 - y1) * 360 + (m2 - m1) * 30 + (day2 - day1)


def add_months(d: date, months: int) -> date:
    """Suma (o resta, si months es negativo) meses a una fecha.

    Se usa para reconstruir el calendario de pagos de cupon caminando
    hacia atras desde el vencimiento (ver Bond.coupon_dates). Si el dia
    del mes no existe en el mes de destino (ej. 31 de abril), lo ajusta
    al ultimo dia valido de ese mes.
    """
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
    """Representa un bono bullet de cupon fijo, con calls opcionales.

    Atributos:
        coupon_pct: cupon anual en % (ej. 7.9 significa 7.90% anual).
        maturity:   fecha de vencimiento (ahi se paga el ultimo cupon + capital).
        face:       valor nominal / "cara" del bono, casi siempre 100.
        freq:       pagos de cupon por año (2 = semestral, el caso tipico).
        calls:      lista opcional de (fecha_call, precio_call) - fechas
                    en las que el emisor puede rescatar el bono antes del
                    vencimiento, y a que precio. Vacia si es bullet puro.
        amortization: lista opcional de (fecha, fraccion) - fechas en las
                    que se repaga una fraccion del capital ORIGINAL (ej.
                    1/3 en cada una de 3 cuotas). Vacia si no amortiza
                    (paga el 100% del capital de golpe al vencimiento).
    """

    coupon_pct: float
    maturity: date
    face: float = 100.0
    freq: int = 2
    calls: list = field(default_factory=list)
    amortization: list = field(default_factory=list)

    @property
    def redemption_scenarios(self) -> list[tuple[date, float]]:
        """Todas las formas en que el bono puede terminar: el vencimiento
        normal (maturity, face) mas cada call cargado. Se usan para
        calcular yield/precio "to worst" (ver docstring del modulo)."""
        return [(self.maturity, self.face)] + list(self.calls)

    def _outstanding_at(self, d: date) -> float:
        """Capital vigente (sobre el face original) despues de aplicar
        cualquier amortizacion con fecha <= d. Sin amortizacion, siempre
        es self.face (el capital nunca baja antes del vencimiento)."""
        if not self.amortization:
            return self.face
        pagado = sum(frac for (fecha, frac) in self.amortization if fecha <= d)
        return self.face * (1 - pagado)

    def coupon_dates(self, settlement: date) -> list[date]:
        """Reconstruye el calendario de pagos de cupon.

        Arranca en el vencimiento y va restando "step" meses (6 si es
        semestral) hasta pasarse de la fecha de settlement. Devuelve la
        lista ordenada cronologicamente: [cupon anterior al settlement,
        ...cupones futuros..., vencimiento].
        """
        step = 12 // self.freq
        dates = [self.maturity]
        cursor = self.maturity
        while True:
            prev = add_months(cursor, -step)
            dates.append(prev)
            if prev <= settlement:
                break
            cursor = prev
        dates.reverse()
        return dates

    def schedule(self, settlement: date):
        """Ubica al settlement dentro del calendario de cupones.

        Devuelve:
            prev_coupon:   fecha del ultimo cupon ya pagado (antes de settlement).
            next_coupon:   fecha del proximo cupon a cobrar.
            future:        lista de todas las fechas de pago que quedan (incluye vencimiento).
            period_days:   duracion del periodo de cupon actual, en dias 30/360.
            accrued_days:  dias ya transcurridos dentro de ese periodo (desde prev_coupon).
            f:             fraccion del periodo que falta para el proximo cupon (0 a 1).
        """
        dates = self.coupon_dates(settlement)
        prev_coupon = dates[0]
        future = [d for d in dates[1:] if d > settlement]
        next_coupon = future[0]
        period_days = days_30_360(prev_coupon, next_coupon) or (360 // self.freq)
        accrued_days = days_30_360(prev_coupon, settlement)
        f = (period_days - accrued_days) / period_days
        return prev_coupon, next_coupon, future, period_days, accrued_days, f

    def cashflows(self, settlement: date, redemption_date: date = None,
                  redemption_price: float = None) -> pd.DataFrame:
        """Tabla de todos los pagos futuros del bono desde el settlement.

        Por defecto asume que el bono llega al vencimiento normal. Si se
        pasan `redemption_date`/`redemption_price`, genera los flujos
        como si el bono se rescatara (call) ahi en cambio: cupones
        normales hasta esa fecha, y ahi un flujo final de
        `redemption_price` (en vez de seguir hasta el vencimiento). Si
        `redemption_date` no coincide con una fecha de cupon exacta (caso
        raro, pero posible), el ultimo flujo incluye el cupon corrido
        (30/360) hasta ese dia.

        La columna "periodos_semestrales" es el tiempo hasta ese flujo
        medido en cantidad de periodos de cupon (no en años ni en dias) -
        es la unidad que se usa para descontar a valor presente.

        Los numeros salen SIN redondear: esta tabla la reusan dirty_price()
        y duration_convexity() para calcular precio/duration, y redondear
        aca adentro introduciria un error chico pero real en esas cuentas.
        El redondeo a 3 decimales es cosa de la interfaz (bonos_pyg_app.py),
        no del motor de calculo.
        """
        prev_coupon, _, future, _, _, f = self.schedule(settlement)

        # Caso bono amortizante: se activa tanto si no se paso ningun
        # escenario de redencion (redemption_date=None) COMO si el que se
        # paso es simplemente el vencimiento normal (asi lo llaman
        # price_to_worst/yield_to_worst para el escenario "sin call") -
        # en ninguno de los dos casos hay un call de por medio, asi que
        # corresponde respetar la amortizacion. Los bonos con call en
        # esta app no amortizan, asi que no hace falta combinar ambas
        # cosas para una fecha que sea a la vez call Y amortizante.
        if redemption_date in (None, self.maturity) and self.amortization:
            outstanding = self._outstanding_at(prev_coupon)
            rows = []
            for i, d in enumerate(future):
                t = f + i
                coupon_amt = self.coupon_pct / 100 / self.freq * outstanding
                amort_frac = next((frac for (fecha, frac) in self.amortization if fecha == d), 0.0)
                principal = self.face * amort_frac
                rows.append({
                    "fecha": d,
                    "dias_desde_settlement_30_360": days_30_360(settlement, d),
                    "periodos_semestrales": t,
                    "cupon": coupon_amt,
                    "principal": principal,
                    "flujo_total": coupon_amt + principal,
                })
                outstanding -= principal  # el capital que queda paga los cupones siguientes
            return pd.DataFrame(rows)

        if redemption_date is None:
            redemption_date, redemption_price = self.maturity, self.face

        coupon_amt = self.coupon_pct / 100 / self.freq * self.face
        period_nominal = 360 / self.freq
        rows = []
        last_date, last_t = prev_coupon, f - 1
        for i, d in enumerate(future):
            t = f + i
            if d < redemption_date:
                rows.append({
                    "fecha": d,
                    "dias_desde_settlement_30_360": days_30_360(settlement, d),
                    "periodos_semestrales": t,
                    "cupon": coupon_amt,
                    "principal": 0.0,
                    "flujo_total": coupon_amt,
                })
                last_date, last_t = d, t
            elif d == redemption_date:
                rows.append({
                    "fecha": d,
                    "dias_desde_settlement_30_360": days_30_360(settlement, d),
                    "periodos_semestrales": t,
                    "cupon": coupon_amt,
                    "principal": redemption_price,
                    "flujo_total": coupon_amt + redemption_price,
                })
                return pd.DataFrame(rows)
            else:
                # el rescate cae ANTES de este cupon (no coincide con el
                # calendario regular): armamos un flujo final con el
                # cupon corrido (30/360) desde el ultimo cupon pagado -
                # el tenedor cobra ese interes prorrateado, no cero.
                stub_days = days_30_360(last_date, redemption_date)
                t_redencion = last_t + stub_days / period_nominal
                cupon_corrido = coupon_amt * stub_days / period_nominal
                rows.append({
                    "fecha": redemption_date,
                    "dias_desde_settlement_30_360": days_30_360(settlement, redemption_date),
                    "periodos_semestrales": t_redencion,
                    "cupon": cupon_corrido,
                    "principal": redemption_price,
                    "flujo_total": redemption_price + cupon_corrido,
                })
                return pd.DataFrame(rows)
        return pd.DataFrame(rows)

    def accrued_interest(self, settlement: date) -> float:
        """Interes corrido: la parte del cupon actual ya "devengada" pero
        todavia no pagada, proporcional a los dias transcurridos desde el
        ultimo cupon. Esto es lo que se le suma al precio limpio para
        llegar al precio sucio (lo que realmente se paga). No depende de
        si el bono termina en un call o en el vencimiento normal. Si el
        bono amortiza, el cupon corriente se calcula sobre el capital
        vigente en ESE periodo (ya reducido por amortizaciones previas),
        no sobre el capital original."""
        prev_coupon, _, _, period_days, accrued_days, _ = self.schedule(settlement)
        outstanding = self._outstanding_at(prev_coupon)
        coupon_amt = self.coupon_pct / 100 / self.freq * outstanding
        return coupon_amt * accrued_days / period_days

    def dirty_price(self, ytm_pct: float, settlement: date, redemption_date: date = None,
                     redemption_price: float = None) -> float:
        """Precio sucio dado un yield: se descuentan todos los flujos
        futuros (cashflows) a la tasa ytm_pct y se suman (valor presente).
        Por defecto asume vencimiento normal; ver `cashflows()` para el
        significado de `redemption_date`/`redemption_price`."""
        cf = self.cashflows(settlement, redemption_date, redemption_price)
        y2 = ytm_pct / 100 / self.freq  # tasa por periodo (semestral)
        pv = cf["flujo_total"] / (1 + y2) ** cf["periodos_semestrales"]
        return float(pv.sum())

    def clean_price(self, ytm_pct: float, settlement: date, redemption_date: date = None,
                     redemption_price: float = None) -> float:
        """Precio limpio = precio sucio menos el interes ya corrido."""
        return self.dirty_price(ytm_pct, settlement, redemption_date, redemption_price) - self.accrued_interest(settlement)

    def paridad(self, clean_price: float, settlement: date) -> float:
        """Paridad = precio sucio / valor tecnico, en %.

        El "valor tecnico" es el capital vigente mas el interes corrido:
        es cuanto "deberia" valer el bono en libros en ese instante, sin
        considerar mercado. Si el bono ya amortizo parte del capital, el
        valor tecnico usa lo que queda vigente, no el capital original.
        Paridad > 100% = el mercado lo paga por encima de su valor
        tecnico; < 100% = por debajo.
        """
        prev_coupon, _, _, _, _, _ = self.schedule(settlement)
        outstanding = self._outstanding_at(prev_coupon)
        accrued = self.accrued_interest(settlement)
        valor_tecnico = outstanding + accrued
        dirty = clean_price + accrued
        return dirty / valor_tecnico * 100

    def yield_from_clean_price(self, clean_price: float, settlement: date,
                                tol: float = 1e-8, max_iter: int = 100,
                                redemption_date: date = None, redemption_price: float = None) -> float:
        """Camino inverso: dado un precio limpio, encuentra el yield que lo
        produce (para un escenario de redencion dado - por defecto, el
        vencimiento normal). No hay formula cerrada para esto, asi que se
        resuelve por busqueda binaria (bisection): como el precio cae
        cuando el yield sube (relacion monotona), se va acotando el
        intervalo [lo, hi] hasta que el precio que da "mid" esta lo
        bastante cerca del precio buscado.
        """
        lo, hi = -5.0, 40.0
        for _ in range(max_iter):
            mid = (lo + hi) / 2
            if self.clean_price(mid, settlement, redemption_date, redemption_price) > clean_price:
                lo = mid  # el precio a "mid" es muy alto -> el yield real es mayor
            else:
                hi = mid  # el precio a "mid" es muy bajo -> el yield real es menor
            if abs(hi - lo) < tol:
                break
        return (lo + hi) / 2

    def price_to_worst(self, ytm_pct: float, settlement: date) -> float:
        """Entre el vencimiento normal y cada call cargado, el "peor"
        precio para el tenedor a un yield dado es el MAS BAJO de todos
        los escenarios. Si no hay calls, es identico a clean_price()."""
        escenarios = [(d, p) for d, p in self.redemption_scenarios if d > settlement]
        return min(self.clean_price(ytm_pct, settlement, d, p) for d, p in escenarios)

    def yield_to_worst(self, clean_price: float, settlement: date) -> float:
        """Analogo pero al reves: el "peor" yield para el tenedor a un
        precio dado es el MAS BAJO entre todos los escenarios posibles.
        Si no hay calls, es identico a yield_from_clean_price()."""
        escenarios = [(d, p) for d, p in self.redemption_scenarios if d > settlement]
        return min(
            self.yield_from_clean_price(clean_price, settlement, redemption_date=d, redemption_price=p)
            for d, p in escenarios
        )

    def worst_scenario(self, settlement: date, ytm_pct: float) -> tuple[date, float]:
        """Cual de los escenarios (vencimiento o algun call) es el que da
        el precio mas bajo a ese yield - o sea, cual es "el peor" en la
        practica. Sirve para mostrarle al usuario cual es (ej. "vence
        normal" vs "se llama tal fecha")."""
        escenarios = [(d, p) for d, p in self.redemption_scenarios if d > settlement]
        return min(escenarios, key=lambda dp: self.clean_price(ytm_pct, settlement, dp[0], dp[1]))

    def duration_convexity(self, ytm_pct: float, settlement: date, redemption_date: date = None,
                            redemption_price: float = None):
        """Sensibilidad del precio ante cambios de yield, para un
        escenario de redencion dado (por defecto, el vencimiento normal).

        - macaulay_years: promedio ponderado (por valor presente) del
          tiempo hasta cada flujo, en años. Es el "centro de gravedad"
          temporal de los pagos del bono.
        - modified_duration: variacion aproximada (%) del precio ante un
          cambio de 1 punto porcentual en el yield. Se usa mas en la
          practica que la Macaulay porque habla directo de precio.
        - convexity: correccion de segundo orden; la relacion precio/yield
          es curva, no lineal, y la convexidad mide esa curvatura.
        """
        cf = self.cashflows(settlement, redemption_date, redemption_price)
        y2 = ytm_pct / 100 / self.freq
        t = cf["periodos_semestrales"]
        pv = cf["flujo_total"] / (1 + y2) ** t
        dirty = pv.sum()
        macaulay_years = float((t * pv).sum() / dirty) / self.freq
        modified_duration = macaulay_years / (1 + y2)
        convexity = float((pv * t * (t + 1)).sum() / dirty) / (1 + y2) ** 2 / self.freq ** 2
        return {
            "macaulay_years": macaulay_years,
            "modified_duration": modified_duration,
            "convexity": convexity,
        }

    def summary(self, settlement: date, clean_price: float = None, ytm_pct: float = None) -> dict:
        """Punto de entrada principal: le pasas precio O yield (uno de los
        dos) y devuelve todo lo demas ya calculado y redondeado a 3
        decimales, listo para mostrar en la interfaz.

        Si el bono tiene calls, el yield/precio devuelto es "to worst"
        (ver docstring del modulo) - se evaluan todos los escenarios
        posibles y se usa el peor para el tenedor. Duration y convexidad
        se calculan contra ESE escenario ganador, no siempre contra el
        vencimiento normal. `escenario_fecha`/`escenario_precio` en el
        resultado indican cual escenario resulto ser el peor.
        """
        if clean_price is None and ytm_pct is None:
            raise ValueError("Pasa clean_price o ytm_pct")

        # Si me dieron precio, calculo el yield que lo explica (y viceversa).
        if ytm_pct is None:
            ytm_pct = self.yield_to_worst(clean_price, settlement)
        if clean_price is None:
            clean_price = self.price_to_worst(ytm_pct, settlement)

        escenario_fecha, escenario_precio = self.worst_scenario(settlement, ytm_pct)

        accrued = self.accrued_interest(settlement)
        dc = self.duration_convexity(ytm_pct, settlement, escenario_fecha, escenario_precio)
        return {
            "settlement": settlement,
            "precio_limpio": round(clean_price, 3),
            "precio_sucio": round(clean_price + accrued, 3),
            "interes_corrido": round(accrued, 3),
            "ytm_pct": round(ytm_pct, 3),
            "duracion_macaulay_anios": round(dc["macaulay_years"], 3),
            "duracion_modificada": round(dc["modified_duration"], 3),
            "convexidad": round(dc["convexity"], 3),
            "escenario_fecha": escenario_fecha,
            "escenario_precio": escenario_precio,
            "es_call": escenario_fecha != self.maturity,
        }
