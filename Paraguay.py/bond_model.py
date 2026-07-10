"""
Motor de calculo para bonos soberanos bullet, cupon fijo, pago semestral,
convencion de dias 30/360 US (la convencion tipica en soberanos emergentes
en USD). Extraido de PY.py para poder reusarlo desde la app de Streamlit
sin duplicar codigo.
"""

from dataclasses import dataclass
from datetime import date
import pandas as pd


def days_30_360(d1: date, d2: date) -> int:
    y1, m1, day1 = d1.year, d1.month, d1.day
    y2, m2, day2 = d2.year, d2.month, d2.day
    if day1 == 31:
        day1 = 30
    if day2 == 31 and day1 == 30:
        day2 = 30
    return (y2 - y1) * 360 + (m2 - m1) * 30 + (day2 - day1)


def add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                       31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


@dataclass
class Bond:
    coupon_pct: float      # cupon anual en % (ej 4.95)
    maturity: date
    face: float = 100.0
    freq: int = 2          # pagos por año

    def coupon_dates(self, settlement: date) -> list[date]:
        """Fechas de cupon desde el vencimiento hacia atras, incluye la
        fecha de cupon anterior al settlement y todas las futuras."""
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
        return dates  # [prev_coupon, ..., future coupons ..., maturity]

    def schedule(self, settlement: date):
        dates = self.coupon_dates(settlement)
        prev_coupon = dates[0]
        future = [d for d in dates[1:] if d > settlement]
        next_coupon = future[0]
        period_days = days_30_360(prev_coupon, next_coupon) or (360 // self.freq)
        accrued_days = days_30_360(prev_coupon, settlement)
        f = (period_days - accrued_days) / period_days  # fraccion de periodo hasta el proximo cupon
        return prev_coupon, next_coupon, future, period_days, accrued_days, f

    def cashflows(self, settlement: date) -> pd.DataFrame:
        """Devuelve un DataFrame con cada flujo futuro: fecha, dias desde
        settlement (30/360), tiempo en periodos semestrales (t) y monto."""
        _, _, future, _, _, f = self.schedule(settlement)
        coupon_amt = self.coupon_pct / 100 / self.freq * self.face
        rows = []
        for i, d in enumerate(future):
            amount = coupon_amt + (self.face if d == self.maturity else 0.0)
            t = f + i  # tiempo en periodos semestrales desde settlement
            rows.append({
                "fecha": d,
                "dias_desde_settlement_30_360": days_30_360(settlement, d),
                "periodos_semestrales": round(t, 3),
                "cupon": round(coupon_amt, 3),
                "principal": self.face if d == self.maturity else 0.0,
                "flujo_total": round(amount, 3),
            })
        return pd.DataFrame(rows)

    def accrued_interest(self, settlement: date) -> float:
        _, _, _, period_days, accrued_days, _ = self.schedule(settlement)
        coupon_amt = self.coupon_pct / 100 / self.freq * self.face
        return coupon_amt * accrued_days / period_days

    def dirty_price(self, ytm_pct: float, settlement: date) -> float:
        cf = self.cashflows(settlement)
        y2 = ytm_pct / 100 / self.freq
        pv = cf["flujo_total"] / (1 + y2) ** cf["periodos_semestrales"]
        return float(pv.sum())

    def clean_price(self, ytm_pct: float, settlement: date) -> float:
        return self.dirty_price(ytm_pct, settlement) - self.accrued_interest(settlement)

    def paridad(self, clean_price: float, settlement: date) -> float:
        """Precio sucio sobre valor tecnico (face + interes corrido), en %."""
        accrued = self.accrued_interest(settlement)
        valor_tecnico = self.face + accrued
        dirty = clean_price + accrued
        return dirty / valor_tecnico * 100

    def yield_from_clean_price(self, clean_price: float, settlement: date,
                                tol: float = 1e-8, max_iter: int = 100) -> float:
        lo, hi = -5.0, 40.0
        for _ in range(max_iter):
            mid = (lo + hi) / 2
            if self.clean_price(mid, settlement) > clean_price:
                lo = mid
            else:
                hi = mid
            if abs(hi - lo) < tol:
                break
        return (lo + hi) / 2

    def duration_convexity(self, ytm_pct: float, settlement: date):
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
        if clean_price is None and ytm_pct is None:
            raise ValueError("Pasa clean_price o ytm_pct")
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
