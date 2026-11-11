import math

XSD = "http://www.w3.org/2001/XMLSchema#"
NUMERIC_DATATYPES = frozenset(XSD + name for name in ("integer", "decimal", "float", "double"))
DATATYPES = NUMERIC_DATATYPES | {XSD + "string", XSD + "boolean"}


def valid_literal(value, datatype: str) -> bool:
    if datatype == XSD + "string":
        return type(value) is str
    if datatype == XSD + "boolean":
        return type(value) is bool
    if datatype == XSD + "integer":
        return type(value) is int
    if datatype in NUMERIC_DATATYPES:
        if type(value) not in (int, float):
            return False
        try:
            finite = math.isfinite(value)
        except OverflowError:
            return False
        return finite and (datatype != XSD + "float" or abs(value) <= 3.4028234663852886e38)
    return False
