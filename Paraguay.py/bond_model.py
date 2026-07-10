"""
MOTOR DE CALCULO DE BONOS (bond_model.py)
==========================================

Este archivo es el "cerebro" matematico de toda la app. No tiene nada de
Streamlit ni de interfaz: solo modela un bono soberano tipico (bullet,
cupon fijo, pago semestral) y sabe convertir entre precio y yield, calcular
cashflows, duration, convexidad y paridad. La app (bonos_pyg_app.py)
importa la clase Bond de aca y solo se encarga de mostrar los numeros.

Conceptos clave para orientarse en el archivo:

- BONO "BULLET": paga cupones fijos periodicos y devuelve el 100% del
  capital (face) recien en la fecha de vencimiento (no amortiza antes).
- CONVENCION ACTUAL/360: para calcular interes corrido y descontar flujos,
  se usan los dias REALES de calendario entre fechas (no una aproximacion
  de "meses de 30 dias"), pero divididos siempre por un denominador fijo
  de 360/freq dias (180 para pago semestral) en vez de por la duracion
  real de cada periodo. Es la convencion que se usa para estos bonos
  puntuales (confirmada por el usuario) - notar que esto es distinto de
  "Actual/Actual", que divide por la duracion real del periodo.
- PRECIO LIMPIO (clean price) vs PRECIO SUCIO (dirty price): el precio
  limpio es el que se cotiza en pantalla/mercado. El precio sucio es lo
  que realmente se paga al comprar el bono, e incluye el interes ya
  devengado desde el ultimo pago de cupon (accrued interest):
      precio sucio = precio limpio + interes corrido
- YIELD (YTM, yield to maturity): la tasa de descuento que iguala el valor
  presente de todos los flujos futuros (cupones + capital) con el precio
  sucio de hoy. Es "la tasa a la que rinde el bono" si lo comprás hoy y lo
  mantenés hasta el vencimiento.
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

from dataclasses import dataclass
from datetime import date
import pandas as pd


# ---------------------------------------------------------------------------
# Funciones de fechas (convencion Actual/360)
# ---------------------------------------------------------------------------
def days_actual(d1: date, d2: date) -> int:
    """Dias reales de calendario entre d1 y d2 (resta de fechas comun).

    A diferencia de 30/360, esto SI cuenta los dias tal como caen en el
    calendario (28, 30 o 31 segun el mes). El "/360" de la convencion
    Actual/360 aparece despues, cuando estos dias se dividen por el
    denominador fijo Bond.nominal_period_days (ver mas abajo).
    """
    return (d2 - d1).days


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
    """Representa un bono bullet de cupon fijo.

    Atributos:
        coupon_pct: cupon anual en % (ej. 7.9 significa 7.90% anual).
        maturity:   fecha de vencimiento (ahi se paga el ultimo cupon + capital).
        face:       valor nominal / "cara" del bono, casi siempre 100.
        freq:       pagos de cupon por año (2 = semestral, el caso tipico).
    """

    coupon_pct: float
    maturity: date
    face: float = 100.0
    freq: int = 2

    @property
    def nominal_period_days(self) -> float:
        """Denominador fijo de la convencion Actual/360 para un periodo de
        cupon: 360/freq (180 dias para pago semestral). Es "nominal" porque
        NO es la cantidad real de dias que tiene ese periodo en el
        calendario (que puede ser 181, 184, etc.) - Actual/360 siempre usa
        este numero fijo como denominador, a proposito.
        """
        return 360 / self.freq

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
            period_days:   dias REALES de calendario que tiene el periodo actual (informativo).
            accrued_days:  dias reales ya transcurridos dentro de ese periodo (desde prev_coupon).
            f:             fraccion (Actual/360) del periodo que falta para el proximo cupon.
        """
        dates = self.coupon_dates(settlement)
        prev_coupon = dates[0]
        future = [d for d in dates[1:] if d > settlement]
        next_coupon = future[0]
        period_days = days_actual(prev_coupon, next_coupon)
        accrued_days = days_actual(prev_coupon, settlement)
        remaining_days = days_actual(settlement, next_coupon)
        f = remaining_days / self.nominal_period_days
        return prev_coupon, next_coupon, future, period_days, accrued_days, f

    def cashflows(self, settlement: date) -> pd.DataFrame:
        """Tabla de todos los pagos futuros del bono desde el settlement.

        Cada fila es un pago de cupon (y el ultimo ademas incluye el
        capital). La columna "periodos_semestrales" es el tiempo hasta ese
        flujo medido en cantidad de periodos de cupon (no en años ni en
        dias) - es la unidad que se usa para descontar a valor presente.
        """
        _, _, future, _, _, f = self.schedule(settlement)
        coupon_amt = self.coupon_pct / 100 / self.freq * self.face
        rows = []
        for i, d in enumerate(future):
            amount = coupon_amt + (self.face if d == self.maturity else 0.0)
            t = f + i  # tiempo en periodos de cupon desde el settlement
            rows.append({
                "fecha": d,
                "dias_desde_settlement": days_actual(settlement, d),
                "periodos_semestrales": round(t, 3),
                "cupon": round(coupon_amt, 3),
                "principal": self.face if d == self.maturity else 0.0,
                "flujo_total": round(amount, 3),
            })
        return pd.DataFrame(rows)

    def accrued_interest(self, settlement: date) -> float:
        """Interes corrido: la parte del cupon actual ya "devengada" pero
        todavia no pagada, proporcional a los dias reales transcurridos
        desde el ultimo cupon (Actual/360: dias reales / 180). Esto es lo
        que se le suma al precio limpio para llegar al precio sucio (lo
        que realmente se paga)."""
        _, _, _, period_days, accrued_days, _ = self.schedule(settlement)
        coupon_amt = self.coupon_pct / 100 / self.freq * self.face
        return coupon_amt * accrued_days / self.nominal_period_days

    def dirty_price(self, ytm_pct: float, settlement: date) -> float:
        """Precio sucio dado un yield: se descuentan todos los flujos
        futuros (cashflows) a la tasa ytm_pct y se suman (valor presente)."""
        cf = self.cashflows(settlement)
        y2 = ytm_pct / 100 / self.freq  # tasa por periodo (semestral)
        pv = cf["flujo_total"] / (1 + y2) ** cf["periodos_semestrales"]
        return float(pv.sum())

    def clean_price(self, ytm_pct: float, settlement: date) -> float:
        """Precio limpio = precio sucio menos el interes ya corrido."""
        return self.dirty_price(ytm_pct, settlement) - self.accrued_interest(settlement)

    def paridad(self, clean_price: float, settlement: date) -> float:
        """Paridad = precio sucio / valor tecnico, en %.

        El "valor tecnico" es el capital vigente (face) mas el interes
        corrido: es cuanto "deberia" valer el bono en libros en ese
        instante, sin considerar mercado. Paridad > 100% = el mercado lo
        paga por encima de su valor tecnico; < 100% = por debajo.
        """
        accrued = self.accrued_interest(settlement)
        valor_tecnico = self.face + accrued
        dirty = clean_price + accrued
        return dirty / valor_tecnico * 100

    def yield_from_clean_price(self, clean_price: float, settlement: date,
                                tol: float = 1e-8, max_iter: int = 100) -> float:
        """Camino inverso: dado un precio limpio, encuentra el yield que lo
        produce. No hay formula cerrada para esto, asi que se resuelve por
        busqueda binaria (bisection): como el precio cae cuando el yield
        sube (relacion monotona), se va acotando el intervalo [lo, hi]
        hasta que el precio que da "mid" esta lo bastante cerca del
        precio buscado.
        """
        lo, hi = -5.0, 40.0
        for _ in range(max_iter):
            mid = (lo + hi) / 2
            if self.clean_price(mid, settlement) > clean_price:
                lo = mid  # el precio a "mid" es muy alto -> el yield real es mayor
            else:
                hi = mid  # el precio a "mid" es muy bajo -> el yield real es menor
            if abs(hi - lo) < tol:
                break
        return (lo + hi) / 2

    def duration_convexity(self, ytm_pct: float, settlement: date):
        """Sensibilidad del precio ante cambios de yield.

        - macaulay_years: promedio ponderado (por valor presente) del
          tiempo hasta cada flujo, en años. Es el "centro de gravedad"
          temporal de los pagos del bono.
        - modified_duration: variacion aproximada (%) del precio ante un
          cambio de 1 punto porcentual en el yield. Se usa mas en la
          practica que la Macaulay porque habla directo de precio.
        - convexity: correccion de segundo orden; la relacion precio/yield
          es curva, no lineal, y la convexidad mide esa curvatura.
        """
        cf = self.cashflows(settlement)
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
        """
        if clean_price is None and ytm_pct is None:
            raise ValueError("Pasa clean_price o ytm_pct")

        # Si me dieron precio, calculo el yield que lo explica (y viceversa).
        if ytm_pct is None:
            ytm_pct = self.yield_from_clean_price(clean_price, settlement)
        if clean_price is None:
            clean_price = self.clean_price(ytm_pct, settlement)

        accrued = self.accrued_interest(settlement)
        dc = self.duration_convexity(ytm_pct, settlement)
        return {
            "settlement": settlement,
            "precio_limpio": round(clean_price, 3),
            "precio_sucio": round(clean_price + accrued, 3),
            "interes_corrido": round(accrued, 3),
            "ytm_pct": round(ytm_pct, 3),
            "duracion_macaulay_anios": round(dc["macaulay_years"], 3),
            "duracion_modificada": round(dc["modified_duration"], 3),
            "convexidad": round(dc["convexity"], 3),
        }
