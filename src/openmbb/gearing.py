"""Gearing math — pure functions, no hardware, no GUI.

Final-drive ratio = rear teeth / front teeth. The MBB's speedo/odo constants
are spfront (front teeth), sprear (rear teeth), and rwhcirc (rear wheel rolling
circumference in mm). Setting spfront/sprear to the physically installed teeth
keeps speed/odometer correct after a re-gear; rwhcirc is trimmed against GPS.
"""

# Known setups for this platform, ratio -> description.
KNOWN_SETUPS = {
    4.50: "stock 20T/90T belt",
    4.09: "22T front / stock 90T (needs ~159T belt)",
    4.00: "re-gear target (22T/88T belt or 14T/56T chain)",
}

DEFAULT_CIRC_MM = 1966   # effective rear-wheel circumference from the ride logs
STOCK_RATIO = 4.50


def ratio(front, rear):
    """Final-drive ratio for a front/rear tooth count."""
    if not front:
        raise ValueError("front teeth must be > 0")
    return rear / float(front)


def revs_per_km(gear_ratio, circ_mm=DEFAULT_CIRC_MM):
    """Motor revolutions per km for a ratio + rear-wheel circumference."""
    return gear_ratio * 1_000_000.0 / float(circ_mm)


def nearest_known(gear_ratio):
    """(description, delta) of the closest known setup to this ratio."""
    if gear_ratio is None:
        return None, None
    best = min(KNOWN_SETUPS, key=lambda k: abs(k - gear_ratio))
    return KNOWN_SETUPS[best], abs(best - gear_ratio)


def gearing_plan(front, rear, circ_mm=DEFAULT_CIRC_MM, ref_ratio=STOCK_RATIO):
    """Full plan for a proposed gearing: the ratio, the exact MBB settings to
    write, and how it compares to a reference ratio (stock by default).

    A LOWER ratio than stock = taller gearing = higher top speed / less
    acceleration and less motor heat at a given road speed.
    """
    if circ_mm <= 0:
        raise ValueError("wheel circumference must be > 0")
    r = ratio(front, rear)
    change = (r / ref_ratio - 1.0) * 100.0
    return {
        "front": int(front),
        "rear": int(rear),
        "ratio": r,
        "spfront": int(front),
        "sprear": int(rear),
        "rwhcirc": int(round(circ_mm)),
        "ref_ratio": ref_ratio,
        "vs_ref_pct": change,                 # vs ref: negative = taller (lower ratio)
        "taller_than_ref": r < ref_ratio,
        "top_speed_factor": ref_ratio / r,    # >1 = higher top speed than ref
        "revs_per_km": revs_per_km(r, circ_mm),
        "nearest": nearest_known(r)[0],
    }


def describe_plan(plan):
    """One-line human summary of a gearing_plan() result."""
    direction = "taller (faster top end, softer launch)" if plan["taller_than_ref"] \
        else "shorter (quicker launch, lower top end)"
    return ("%dT/%dT = %.3f:1 — %+.1f%% vs %.2f:1, %s. "
            "Write spfront=%d, sprear=%d, rwhcirc=%d mm."
            % (plan["front"], plan["rear"], plan["ratio"], plan["vs_ref_pct"],
               plan.get("ref_ratio", STOCK_RATIO), direction, plan["spfront"],
               plan["sprear"], plan["rwhcirc"]))
