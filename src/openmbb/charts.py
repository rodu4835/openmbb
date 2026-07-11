"""Tiny dependency-free helpers for the Analyze charts.

Only pure math lives here (nice axis bounds, downsampling, min/max) so it can be
unit-tested; the actual drawing is done on a tk.Canvas in the GUI. No numpy / no
matplotlib — the frozen build stays small.
"""

import math


def nice_bounds(lo, hi, ticks=5):
    """Round [lo, hi] out to 'nice' axis limits and return (nlo, nhi, step).

    step is a 1/2/5 x 10^n value so gridlines land on readable numbers.
    """
    if not (isinstance(lo, (int, float)) and isinstance(hi, (int, float))):
        return 0.0, 1.0, 1.0
    if hi < lo:
        lo, hi = hi, lo
    if hi == lo:                      # a flat series still needs a visible band
        hi = lo + (abs(lo) * 0.1 or 1.0)
    raw = (hi - lo) / max(1, ticks)
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1.0
    norm = raw / mag
    if norm < 1.5:
        step = 1 * mag
    elif norm < 3:
        step = 2 * mag
    elif norm < 7:
        step = 5 * mag
    else:
        step = 10 * mag
    nlo = math.floor(lo / step) * step
    nhi = math.ceil(hi / step) * step
    return nlo, nhi, step


def axis_ticks(lo, hi, step):
    """The gridline values from lo..hi inclusive (guards against runaway loops)."""
    if step <= 0:
        return [lo, hi]
    out = []
    v = lo
    # +0.5 step of slack so floating error doesn't drop the last tick
    while v <= hi + step * 0.5 and len(out) < 1000:
        out.append(round(v, 10))
        v += step
    return out


def downsample(points, max_points=600):
    """Reduce a long series to at most max_points by uniform striding (keeps the
    first and last sample)."""
    n = len(points)
    if n <= max_points or max_points < 2:
        return list(points)
    stride = (n - 1) / (max_points - 1)
    return [points[min(n - 1, int(round(i * stride)))] for i in range(max_points)]


def series_from(records, xkey, ykey):
    """(x, y) pairs from ride records where BOTH fields are present, sorted by x."""
    pts = [(r.get(xkey), r.get(ykey)) for r in records]
    pts = [(x, y) for (x, y) in pts if isinstance(x, (int, float))
           and isinstance(y, (int, float))]
    pts.sort(key=lambda p: p[0])
    return pts
