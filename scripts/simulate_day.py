"""Compare today's dispatch (nearest driver, nearest stop next) with route planning on a simulated day.

Plan 1, step 9. Uses estimated driving times only (straight line x 1.3 at FALLBACK_SPEED_KMH): no Google, no
database, no Telegram. The same random day is replayed with each rule: today's, and route planning with overload
rule A (delay shared) and rule B (most customers on time, `--late-penalty-min` per late customer).

    .venv\\Scripts\\python.exe scripts\\simulate_day.py [--drivers 2] [--orders 100] [--hours 10] [--radius-km 4]
        [--service-min 5] [--max-wait-min 60] [--late-penalty-min 60] [--seed 1]
"""

import argparse
import math
import random
import statistics
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.services import optimizer  # noqa: E402
from app.services.optimizer import Stop  # noqa: E402
from app.services.travel import Leg, estimate_seconds, straight_line_m  # noqa: E402

CENTRE = (53.8008, -1.5491)
T0 = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


@dataclass
class Order:
    id: int
    placed: float  # seconds since the start of the day
    at: tuple[float, float]
    delivered: float | None = None


@dataclass
class Driver:
    id: int
    pos: tuple[float, float] = CENTRE
    free_at: float = 0.0  # when they finish the stop they're driving to / at
    heading: Order | None = None  # the locked stop
    queue: list[Order] = field(default_factory=list)  # the stops after it
    driven: float = 0.0


def random_point(rng: random.Random, radius_km: float) -> tuple[float, float]:
    r, a = radius_km * math.sqrt(rng.random()), rng.uniform(0, 2 * math.pi)
    return (CENTRE[0] + r * math.sin(a) / 111.2, CENTRE[1] + r * math.cos(a) / (111.2 * math.cos(math.radians(CENTRE[0]))))


def secs(a, b) -> int:
    return estimate_seconds(a, b)


def plan_cost(d: Driver, stops: list[Order], seq: list[Order], now: float, service: int, wait: int) -> optimizer.Plan:
    """The route plan (finish time, lateness) for visiting `seq` after the driver's current stop."""
    start = d.heading.at if d.heading else d.pos
    start_time = T0 + timedelta(seconds=max(now, d.free_at))
    pts = [o.at for o in stops]
    from_start = [Leg(secs(start, p), "estimate") for p in pts]
    legs = [[Leg(secs(p, q), "estimate") for q in pts] for p in pts]
    ostops = [Stop(o.id, o.at, False, T0 + timedelta(seconds=o.placed + wait)) for o in stops]
    return optimizer.evaluate(start_time, from_start, legs, ostops, service, [o.id for o in seq]), (start_time, from_start, legs, ostops)


def best_gap(d: Driver, new: Order, now: float, service: int, wait: int) -> tuple[list[Order], optimizer.Plan]:
    stops = [*d.queue, new]
    options = [d.queue[:k] + [new] + d.queue[k:] for k in range(len(d.queue) + 1)]
    plans = [(seq, plan_cost(d, stops, seq, now, service, wait)[0]) for seq in options]
    return min(plans, key=lambda p: p[1].cost)


def late_total(plan: optimizer.Plan) -> float:
    return sum(plan.late.values(), timedelta(0)).total_seconds()


def fewer_late(a: optimizer.Plan, b: optimizer.Plan) -> bool:
    """As planning.fewer_late: fewer late customers (rule B), else fewer minutes late (rule A)."""
    if settings.late_customer_penalty_minutes > 0 and len(a.late) != len(b.late):
        return len(a.late) < len(b.late)
    return late_total(a) < late_total(b)


def simulate(orders: list[Order], n_drivers: int, planned: bool, service: int, wait: int, threshold: int) -> dict:
    orders = [Order(o.id, o.placed, o.at) for o in orders]
    drivers = [Driver(i) for i in range(n_drivers)]
    pending = list(orders)
    by_id = {o.id: o for o in orders}

    def start_next(d: Driver, now: float) -> None:
        """Driver finished (or is idle): drive to the next stop."""
        if d.heading:
            d.pos, d.heading = d.heading.at, None
        if not d.queue:
            return
        if planned:
            nxt = d.queue.pop(0)
        else:
            nxt = min(d.queue, key=lambda o: straight_line_m(d.pos, o.at))  # today: nearest stop next
            d.queue.remove(nxt)
        drive = secs(d.pos, nxt.at)
        d.driven += drive
        nxt.delivered = max(now, d.free_at) + drive
        d.free_at = nxt.delivered + service
        d.heading = nxt

    now = 0.0
    while pending or any(d.heading or d.queue for d in drivers):
        next_order = pending[0].placed if pending else math.inf
        next_free = min((d.free_at for d in drivers if d.heading), default=math.inf)
        if next_free <= next_order:
            now = next_free
            for d in drivers:
                if d.heading and d.free_at <= now:
                    start_next(d, now)
            continue
        now = next_order
        o = pending.pop(0)
        if planned:
            # Driver who'd finish least later while keeping deadlines (step 8); best gap, reshuffle if worth it (step 4)
            options = []
            for d in drivers:
                seq, plan = best_gap(d, o, now, service, wait)
                base = plan_cost(d, d.queue, d.queue, now, service, wait)[0] if d.queue else None
                finish_before = base.finish if base else T0 + timedelta(seconds=max(now, d.free_at))
                # As planning.Offer.key: rule B fewest newly late customers, rule A any new lateness; then minutes late
                newly_late = len(plan.late) - (len(base.late) if base else 0)
                more_late = late_total(plan) - (late_total(base) if base else 0)
                first = newly_late if settings.late_customer_penalty_minutes > 0 else more_late > 0
                options.append((first, more_late, (plan.finish - finish_before).total_seconds(), d.id, d, seq, plan))
            *_, d, seq, plan = min(options, key=lambda x: x[:4])
            stops = [*d.queue, o]
            _, inputs = plan_cost(d, stops, seq, now, service, wait)
            best = optimizer.solve(*inputs, service, time_limit=timedelta(milliseconds=200))
            if plan.cost - best.cost >= threshold or fewer_late(best, plan):
                seq = [by_id[i] for i in best.sequence]
            d.queue = seq
        else:
            # Today: nearest driver (to where they are / are heading)
            d = min(drivers, key=lambda d: straight_line_m(d.heading.at if d.heading else d.pos, o.at))
            d.queue.append(o)
        if d.heading is None:
            start_next(d, now)

    waits = sorted((o.delivered - o.placed) / 60 for o in orders)
    late = [(o.delivered - o.placed - wait) / 60 for o in orders if o.delivered - o.placed > wait]
    hours = math.ceil(max(o.placed for o in orders) / 3600)
    return {
        "waits": waits,
        "late_minutes": sum(late),
        "by_hour": [
            [((o.delivered - o.placed) / 60) for o in orders if h * 3600 <= o.placed < (h + 1) * 3600] for h in range(hours)
        ],
        "within": sum(w <= wait / 60 for w in waits) / len(waits),
        "avg": statistics.mean(waits),
        "p90": waits[int(0.9 * (len(waits) - 1))],
        "max": waits[-1],
        "driving_h": sum(d.driven for d in drivers) / 3600,
        "done_h": max(d.free_at for d in drivers) / 3600,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--drivers", type=int, default=2)
    ap.add_argument("--orders", type=int, default=100)
    ap.add_argument("--hours", type=float, default=10)
    ap.add_argument("--radius-km", type=float, default=4)
    ap.add_argument("--service-min", type=float, default=5)
    ap.add_argument("--max-wait-min", type=float, default=60)
    ap.add_argument("--threshold-min", type=float, default=3)
    ap.add_argument("--late-penalty-min", type=float, default=60, help="rule B: each late customer counts as this many minutes late")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--detail", action="store_true", help="also show waiting bands and each hour")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    orders = sorted(
        (Order(i + 1, rng.uniform(0, a.hours * 3600), random_point(rng, a.radius_km)) for i in range(a.orders)), key=lambda o: o.placed
    )
    orders = [Order(i + 1, o.placed, o.at) for i, o in enumerate(orders)]
    service, wait, threshold = int(a.service_min * 60), int(a.max_wait_min * 60), int(a.threshold_min * 60)
    print(f"{a.drivers} drivers, {a.orders} orders over {a.hours:g} h, within {a.radius_km:g} km, {a.service_min:g} min per stop, "
          f"limit {a.max_wait_min:g} min (seed {a.seed})")
    rows = [("Today (nearest first)", simulate(orders, a.drivers, False, service, wait, threshold))]
    for name, penalty in (("Planning A (shared)", 0), (f"Planning B ({a.late_penalty_min:g} min)", a.late_penalty_min)):
        settings.late_customer_penalty_minutes = penalty
        rows.append((name, simulate(orders, a.drivers, True, service, wait, threshold)))
    print(f"{'':24}{'within limit':>13}{'avg wait':>10}{'90% within':>12}{'worst':>8}{'driving':>10}{'last done':>11}")
    for name, r in rows:
        print(f"{name:24}{r['within']:>12.0%} {r['avg']:>8.0f}m {r['p90']:>10.0f}m {r['max']:>6.0f}m {r['driving_h']:>8.1f}h {r['done_h']:>9.1f}h")
    if not a.detail:
        return
    bands = [(0, 30), (30, 60), (60, 90), (90, 120), (120, 180), (180, 300), (300, 10_000)]
    print()
    print("Customers by waiting time (placed -> delivered):")
    print(f"{'':24}" + "".join(f"{f'{lo}-{hi}m' if hi < 10_000 else f'{lo}m+':>10}" for lo, hi in bands) + f"{'late min total':>16}")
    for name, r in rows:
        counts = [sum(lo <= w < hi for w in r["waits"]) for lo, hi in bands]
        print(f"{name:24}" + "".join(f"{c:>10}" for c in counts) + f"{r['late_minutes']:>16.0f}")
    print()
    print("Each hour's new orders: how many were delivered within the limit, and their worst wait:")
    print(f"{'placed in hour':24}" + "".join(f"{h + 1:>8}" for h in range(len(rows[0][1]['by_hour']))))
    for name, r in rows:
        print(f"{name + ' (within)':24}" + "".join(f"{f'{sum(w <= a.max_wait_min for w in ws)}/{len(ws)}':>8}" for ws in r["by_hour"]))
        print(f"{name + ' (worst)':24}" + "".join(f"{(max(ws) if ws else 0):>7.0f}m" for ws in r["by_hour"]))


if __name__ == "__main__":
    main()
