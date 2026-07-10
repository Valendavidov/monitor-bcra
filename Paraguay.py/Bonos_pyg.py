"""
Modelo de bono - Republica del Paraguay
ISIN: US699149AP51 (identificacion de mejor esfuerzo, ver notas en el chat)
Cupon: 4.950% anual, pagadero semestralmente
Vencimiento: 28-abril-2031
Convencion de dias: 30/360 (bullet bond, sin amortizacion ni opciones)

Requisitos: pandas  ->  pip install pandas
Uso:
    python paraguay_2031_bond.py
Editar la seccion "PARAMETROS" al final del archivo para tu caso de uso.
"""

from dataclasses import dataclass
from datetime import date
import pandas as pd


# ---------------------------------------------------------------------------
# Utilidades de conteo de dias (30/360 US, la convencion tipica en soberanos
# emergentes en USD)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Modelo de bono bullet, cupon fijo, frecuencia semestral
# ---------------------------------------------------------------------------
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
                "periodos_semestrales": round(t, 4),
                "cupon": round(coupon_amt, 6),
                "principal": self.face if d == self.maturity else 0.0,
                "flujo_total": round(amount, 6),
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
            "precio_limpio": round(clean_price, 4),
            "precio_sucio": round(clean_price + accrued, 4),
            "interes_corrido": round(accrued, 4),
            "ytm_pct": round(ytm_pct, 4),
            "duracion_macaulay_anios": round(dc["macaulay_years"], 4),
            "duracion_modificada": round(dc["modified_duration"], 4),
            "convexidad": round(dc["convexity"], 4),
        }


# ---------------------------------------------------------------------------
# PARAMETROS - edita esto segun tu caso
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    bond = Bond(
        coupon_pct=4.95,
        maturity=date(2031, 4, 28),
        face=100.0,
        freq=2,
    )

    settlement = date(2026, 7, 10)   # fecha de liquidacion (t+2 habitual)

    # Opcion A: le das un precio de mercado y te calcula el yield
    clean_price = 95.0
    res_from_price = bond.summary(settlement, clean_price=clean_price)

    # Opcion B: le das un yield y te calcula el precio
    ytm = 6.5
    res_from_yield = bond.summary(settlement, ytm_pct=ytm)

    print("=" * 60)
    print(f"Bono Paraguay {bond.coupon_pct}% {bond.maturity}  |  settlement {settlement}")
    print("=" * 60)

    print(f"\n--- Dado precio limpio = {clean_price} ---")
    for k, v in res_from_price.items():
        print(f"  {k}: {v}")

    print(f"\n--- Dado yield = {ytm}% ---")
    for k, v in res_from_yield.items():
        print(f"  {k}: {v}")

    print("\n--- Cashflows futuros (usando el precio de la opcion A) ---")
    cf = bond.cashflows(settlement)
    pd.set_option("display.width", 120)
    print(cf.to_string(index=False))

    cf.to_csv("cashflows_paraguay_2031.csv", index=False)
    print("\nCashflows exportados a cashflows_paraguay_2031.csv")