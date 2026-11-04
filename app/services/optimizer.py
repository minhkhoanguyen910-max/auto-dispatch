"""The best order of a driver's stops (Plan 1, step 3), with Google OR-Tools.

`solve()` only calculates; it changes nothing. Given where the route starts, the remaining stops, driving times
(from `travel.matrix`) and the time at each stop, it returns the stop sequence and the arrival time at each stop.

What "best" means (agreed): the driver finishes all stops as soon as possible (driving + time at stops; the
route ends at the last stop, there's no return to base). On top of that:
- a missed "deliver by" costs `LATE_WEIGHT` per second late, so deadlines are kept whenever possible; when one
  can't be, the result says by how much instead of failing;
- each customer who'd miss their "deliver by" also costs a fixed amount, as much as `LATE_CUSTOMER_PENALTY_MINUTES`
  of lateness (overload rule B, chosen 2026-10-29): when not everyone can be on time, as many customers as possible
  are, and a few wait longer. 0 = only the minutes count (rule A: the delay is shared);
- an urgent stop's arrival time counts `URGENT_WEIGHT` times, so urgent stops come early.
Stops without a map location can't be planned: they go last, in the order given, without an arrival time.
"""

import itertools
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from app.config import settings
from app.services.travel import Leg, LatLng

# Cost per second, relative to 1 per second of the driver's time
LATE_WEIGHT = 100
URGENT_WEIGHT = 10
# Up to this many stops every order is tried (exact); above, OR-Tools searches for at most TIME_LIMIT
EXACT_UP_TO = 7
TIME_LIMIT = timedelta(seconds=1)
HORIZON = 24 * 3600


@dataclass
class Stop:
    order_id: int
    location: LatLng | None
    urgent: bool = False
    deliver_by: datetime | None = None


@dataclass
class Plan:
    sequence: list[int]  # order ids in visiting order; stops without a location last
    arrivals: dict[int, datetime]  # planned arrival per order id (not for stops without a location)
    late: dict[int, timedelta]  # deadlines that will be missed, and by how much
    finish: datetime  # when the last planned stop is done
    estimated: bool  # some leg of the chosen route is an estimate rather than Google's time
    unlocated: list[int] = field(default_factory=list)
    cost: int = 0  # what the optimizer minimizes (seconds of driver time, plus lateness and urgency weights)


def late_penalty() -> int:
    """The fixed cost of each customer who'd miss their deadline (rule B), in the solver's units."""
    return round(LATE_WEIGHT * settings.late_customer_penalty_minutes * 60)


def _cost(order: list[int], start_secs: list[int], legs: list[list[int]], service: int, deadlines: list[int | None], urgent: list[bool]) -> int:
    """The objective for a visiting order (indexes into the planned stops), in the solver's units."""
    t = cost = 0
    prev = None
    penalty = late_penalty()
    for i in order:
        t += start_secs[i] if prev is None else service + legs[prev][i]
        if deadlines[i] is not None and t > deadlines[i]:
            cost += LATE_WEIGHT * (t - deadlines[i]) + penalty
        if urgent[i]:
            cost += (URGENT_WEIGHT - 1) * t
        prev = i
    return cost + t + service


def solve(
    start_time: datetime,
    from_start: list[Leg],
    legs: list[list[Leg]],
    stops: list[Stop],
    service_seconds: int,
    time_limit: timedelta = TIME_LIMIT,
) -> Plan:
    """`from_start[i]`: driving time from where the route starts to stops[i]; `legs[i][j]`: stops[i] → stops[j]."""
    planned, start_secs, secs, deadlines, urgent = _inputs(start_time, from_start, legs, stops)
    n = len(planned)
    if n == 0:
        order: list[int] = []
    elif n <= EXACT_UP_TO:
        order = list(min(itertools.permutations(range(n)), key=lambda o: _cost(list(o), start_secs, secs, service_seconds, deadlines, urgent)))
    else:
        started = time.monotonic()
        order = _search(start_secs, secs, service_seconds, deadlines, urgent, time_limit / 2 if late_penalty() else time_limit)
        if late_penalty():
            # OR-Tools only knows cost per late second; the fixed cost per late customer is added by improving its answer
            cost = lambda o: _cost(o, start_secs, secs, service_seconds, deadlines, urgent)  # noqa: E731
            order = _improve(order, cost, started + time_limit.total_seconds())
    return evaluate(start_time, from_start, legs, stops, service_seconds, [stops[planned[k]].order_id for k in order])


def evaluate(
    start_time: datetime, from_start: list[Leg], legs: list[list[Leg]], stops: list[Stop], service_seconds: int, sequence: list[int]
) -> Plan:
    """The plan for visiting the stops in a given order (order ids; ones without a location are put last), scored
    the same way `solve` scores its answer, so other orders (e.g. the current one) can be compared with it."""
    planned, start_secs, secs, deadlines, urgent = _inputs(start_time, from_start, legs, stops)
    k_of = {stops[i].order_id: k for k, i in enumerate(planned)}
    order = [k_of[o] for o in sequence if o in k_of]
    order += [k for k in range(len(planned)) if k not in order]  # any planned stop the sequence left out
    unlocated = [stops[i].order_id for i in range(len(stops)) if i not in planned]

    arrivals: dict[int, datetime] = {}
    late: dict[int, timedelta] = {}
    estimated = False
    t = 0
    prev = None
    for k in order:
        i = planned[k]
        leg = from_start[i] if prev is None else legs[planned[prev]][i]
        estimated |= leg.source != "google"
        t += start_secs[k] if prev is None else service_seconds + secs[prev][k]
        stop = stops[i]
        arrivals[stop.order_id] = start_time + timedelta(seconds=t)
        if stop.deliver_by and arrivals[stop.order_id] > stop.deliver_by:
            late[stop.order_id] = arrivals[stop.order_id] - stop.deliver_by
        prev = k
    return Plan(
        sequence=[stops[planned[k]].order_id for k in order] + unlocated,
        arrivals=arrivals,
        late=late,
        finish=start_time + timedelta(seconds=t + service_seconds if order else 0),
        estimated=estimated,
        unlocated=unlocated,
        cost=_cost(order, start_secs, secs, service_seconds, deadlines, urgent) if order else 0,
    )


def _inputs(start_time, from_start, legs, stops):
    """Indexes of the stops that can be planned (have a location and a time from the start), and their times."""
    planned = [i for i, s in enumerate(stops) if s.location is not None and from_start[i].seconds is not None]
    start_secs = [from_start[i].seconds for i in planned]
    # A pair without a time (shouldn't happen for located stops) is treated as very far
    secs = [[(legs[a][b].seconds if a != b else 0) if (a == b or legs[a][b].seconds is not None) else HORIZON for b in planned] for a in planned]
    deadlines = [
        max(0, int((stops[i].deliver_by - start_time).total_seconds())) if stops[i].deliver_by else None for i in planned
    ]
    urgent = [stops[i].urgent for i in planned]
    return planned, start_secs, secs, deadlines, urgent


def _improve(order: list[int], cost, deadline: float) -> list[int]:
    """Local search on the full objective: move one stop elsewhere or swap two, keeping any change that lowers the
    cost, until nothing helps or the time (`deadline`, time.monotonic) is up."""
    best, best_cost = list(order), cost(order)
    improved = True
    while improved and time.monotonic() < deadline:
        improved = False
        n = len(best)
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                moved = best[:i] + best[i + 1 :]
                moved.insert(j, best[i])
                swapped = list(best)
                swapped[i], swapped[j] = swapped[j], swapped[i]
                for candidate in (moved, swapped):
                    c = cost(candidate)
                    if c < best_cost:
                        best, best_cost, improved = candidate, c, True
            if time.monotonic() >= deadline:
                break
    return best


def _search(start_secs, secs, service, deadlines, urgent, time_limit: timedelta) -> list[int]:
    """OR-Tools routing: node 0 = where the route starts, 1..n = stops, n+1 = a free end (no return trip)."""
    n = len(start_secs)
    end = n + 1
    manager = pywrapcp.RoutingIndexManager(n + 2, 1, [0], [end])
    routing = pywrapcp.RoutingModel(manager)

    def transit(from_index: int, to_index: int) -> int:
        a, b = manager.IndexToNode(from_index), manager.IndexToNode(to_index)
        if b == end:
            return service if a != 0 else 0
        if a == 0:
            return start_secs[b - 1] if b else 0
        return service + secs[a - 1][b - 1]

    callback = routing.RegisterTransitCallback(transit)
    routing.SetArcCostEvaluatorOfAllVehicles(callback)
    routing.AddDimension(callback, 0, HORIZON * 4, True, "time")
    time = routing.GetDimensionOrDie("time")
    for k in range(n):
        index = manager.NodeToIndex(k + 1)
        if deadlines[k] is not None:
            time.SetCumulVarSoftUpperBound(index, deadlines[k], LATE_WEIGHT)
        if urgent[k]:
            # Soft bound 0: every second until arrival costs URGENT_WEIGHT - 1 extra
            time.SetCumulVarSoftUpperBound(index, 0, URGENT_WEIGHT - 1)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromMilliseconds(int(time_limit.total_seconds() * 1000))
    solution = routing.SolveWithParameters(params)
    if solution is None:  # never expected (soft limits only); keep the given order rather than fail
        return list(range(n))
    order = []
    index = routing.Start(0)
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        if node not in (0, end):
            order.append(node - 1)
        index = solution.Value(routing.NextVar(index))
    return order
