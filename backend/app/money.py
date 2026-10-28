"""Money is always integer ngwee (K1 = 100 ngwee). Never use floats for amounts."""


def kwacha_to_ngwee(k) -> int:
    return int(round(float(k) * 100))


def fmt_k(ngwee: int) -> str:
    whole, frac = divmod(abs(ngwee), 100)
    s = f"K{whole:,}" if frac == 0 else f"K{whole:,}.{frac:02d}"
    return f"-{s}" if ngwee < 0 else s