from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List
import json
import math

PRODUCTS = ["ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT"]
POS_LIMIT = {
    "ASH_COATED_OSMIUM": 50,
    "INTARIAN_PEPPER_ROOT": 50,
}

OSM_EMA_ALPHA = 0.12
OSM_TAKE_EDGE = 1
OSM_BASE_QTY = 15

PEP_FAST_ALPHA = 2 / (8 + 1)
PEP_SLOW_ALPHA = 2 / (30 + 1)
PEP_TREND_THRESH = 1.5
PEP_TAKE_EDGE = 1
PEP_BASE_QTY = 12
PEP_MAX_TREND_POS = 40


def best_bid(order_depth: OrderDepth):
    if order_depth.buy_orders:
        p = max(order_depth.buy_orders.keys())
        return p, order_depth.buy_orders[p]
    return None, 0


def best_ask(order_depth: OrderDepth):
    if order_depth.sell_orders:
        p = min(order_depth.sell_orders.keys())
        return p, order_depth.sell_orders[p]
    return None, 0


def clamp_buy(qty: int, pos: int, limit: int) -> int:
    return max(0, min(qty, limit - pos))


def clamp_sell(qty: int, pos: int, limit: int) -> int:
    return max(0, min(qty, limit + pos))


class PersistentState:
    def __init__(self):
        self.osm_fv = 10000.0
        self.pep_fast = 0.0
        self.pep_slow = 0.0
        self.pep_init = False

    def to_dict(self):
        return {
            "osm_fv": self.osm_fv,
            "pep_fast": self.pep_fast,
            "pep_slow": self.pep_slow,
            "pep_init": self.pep_init,
        }

    @staticmethod
    def from_dict(d):
        s = PersistentState()
        s.osm_fv = d.get("osm_fv", 10000.0)
        s.pep_fast = d.get("pep_fast", 0.0)
        s.pep_slow = d.get("pep_slow", 0.0)
        s.pep_init = d.get("pep_init", False)
        return s


def take_side(product: str, order_depth: OrderDepth, position: int, fair: float, pstate: PersistentState) -> List[Order]:
    limit = POS_LIMIT[product]
    orders: List[Order] = []

    # Buy cheap asks
    for ask, vol in sorted(order_depth.sell_orders.items()):
        if ask > fair - OSM_TAKE_EDGE:
            break
        if position >= limit:
            break
        qty = min(-vol, clamp_buy(OSM_BASE_QTY, position, limit))
        if qty > 0:
            orders.append(Order(product, ask, qty))
            position += qty

    # Sell rich bids
    for bid, vol in sorted(order_depth.buy_orders.items(), reverse=True):
        if bid < fair + OSM_TAKE_EDGE:
            break
        if position <= -limit:
            break
        qty = min(vol, clamp_sell(OSM_BASE_QTY, position, limit))
        if qty > 0:
            orders.append(Order(product, bid, -qty))
            position -= qty

    return orders


def make_side(product: str, order_depth: OrderDepth, position: int, fair: float) -> List[Order]:
    limit = POS_LIMIT[product]
    orders: List[Order] = []

    bb, _ = best_bid(order_depth)
    ba, _ = best_ask(order_depth)
    if bb is None or ba is None:
        return orders

    inv_skew = (position / limit) * 1.8
    bid_target = math.floor(fair - inv_skew)
    ask_target = math.ceil(fair + inv_skew)

    buy_price = min(bb + 1, bid_target)
    sell_price = max(ba - 1, ask_target)

    if buy_price >= ba:
        buy_price = bb
    if sell_price <= bb:
        sell_price = ba

    buy_qty = clamp_buy(OSM_BASE_QTY, position, limit)
    sell_qty = clamp_sell(OSM_BASE_QTY, position, limit)

    if buy_qty > 0 and buy_price < ba:
        orders.append(Order(product, buy_price, buy_qty))

    if sell_qty > 0 and sell_price > bb:
        orders.append(Order(product, sell_price, -sell_qty))

    return orders


def trade_osm(order_depth: OrderDepth, position: int, pstate: PersistentState) -> List[Order]:
    product = "ASH_COATED_OSMIUM"
    bb, _ = best_bid(order_depth)
    ba, _ = best_ask(order_depth)
    if bb is None or ba is None:
        return []

    mid = (bb + ba) / 2
    pstate.osm_fv = (1 - OSM_EMA_ALPHA) * pstate.osm_fv + OSM_EMA_ALPHA * mid
    fair = pstate.osm_fv

    orders = take_side(product, order_depth, position, fair, pstate)
    new_pos = position + sum(o.quantity for o in orders)
    orders.extend(make_side(product, order_depth, new_pos, fair))
    return orders


def trade_pepper(order_depth: OrderDepth, position: int, pstate: PersistentState) -> List[Order]:
    product = "INTARIAN_PEPPER_ROOT"
    limit = POS_LIMIT[product]
    bb, bb_vol = best_bid(order_depth)
    ba, ba_vol = best_ask(order_depth)
    if bb is None or ba is None:
        return []

    mid = (bb + ba) / 2

    if not pstate.pep_init:
        pstate.pep_fast = mid
        pstate.pep_slow = mid
        pstate.pep_init = True
    else:
        pstate.pep_fast = (1 - PEP_FAST_ALPHA) * pstate.pep_fast + PEP_FAST_ALPHA * mid
        pstate.pep_slow = (1 - PEP_SLOW_ALPHA) * pstate.pep_slow + PEP_SLOW_ALPHA * mid

    fast = pstate.pep_fast
    slow = pstate.pep_slow
    trend = fast - slow

    # Always active: trade the spread even when trend is weak.
    # Trend only changes how aggressive we are.
    if trend > PEP_TREND_THRESH:
        adj_fair = fast + 1.0
        take_buy_edge = PEP_TAKE_EDGE + 1
        take_sell_edge = PEP_TAKE_EDGE + 2
        max_long = PEP_MAX_TREND_POS
    elif trend < -PEP_TREND_THRESH:
        adj_fair = fast - 1.0
        take_buy_edge = PEP_TAKE_EDGE + 2
        take_sell_edge = PEP_TAKE_EDGE + 1
        max_long = PEP_MAX_TREND_POS
    else:
        adj_fair = mid
        take_buy_edge = PEP_TAKE_EDGE
        take_sell_edge = PEP_TAKE_EDGE
        max_long = 25

    orders: List[Order] = []

    # Take cheap asks
    for ask, vol in sorted(order_depth.sell_orders.items()):
        if ask > adj_fair - take_buy_edge:
            break
        if position >= limit:
            break
        qty = min(-vol, clamp_buy(PEP_BASE_QTY, position, limit))
        if trend > PEP_TREND_THRESH:
            qty = min(qty, max(0, max_long - position))
        if qty > 0:
            orders.append(Order(product, ask, qty))
            position += qty

    # Take rich bids
    for bid, vol in sorted(order_depth.buy_orders.items(), reverse=True):
        if bid < adj_fair + take_sell_edge:
            break
        if position <= -limit:
            break
        qty = min(vol, clamp_sell(PEP_BASE_QTY, position, limit))
        if trend < -PEP_TREND_THRESH:
            qty = min(qty, max(0, limit + position))
        if qty > 0:
            orders.append(Order(product, bid, -qty))
            position -= qty

    # Passive quoting
    inv_skew = (position / limit) * 1.2
    buy_price = min(bb + 1, math.floor(adj_fair - inv_skew))
    sell_price = max(ba - 1, math.ceil(adj_fair + inv_skew))

    if buy_price >= ba:
        buy_price = bb
    if sell_price <= bb:
        sell_price = ba

    buy_qty = clamp_buy(PEP_BASE_QTY, position, limit)
    sell_qty = clamp_sell(PEP_BASE_QTY, position, limit)

    # In strong trends, lean harder in the trend direction.
    if trend > PEP_TREND_THRESH:
        buy_qty = min(buy_qty, 15)
        sell_qty = min(sell_qty, 6)
    elif trend < -PEP_TREND_THRESH:
        buy_qty = min(buy_qty, 6)
        sell_qty = min(sell_qty, 15)

    if buy_qty > 0 and buy_price < ba:
        orders.append(Order(product, buy_price, buy_qty))

    if sell_qty > 0 and sell_price > bb:
        orders.append(Order(product, sell_price, -sell_qty))

    return orders


class Trader:
    def run(self, state: TradingState):
        try:
            pstate = PersistentState.from_dict(json.loads(state.traderData)) if state.traderData else PersistentState()
        except Exception:
            pstate = PersistentState()

        result: Dict[str, List[Order]] = {}

        for product in PRODUCTS:
            if product not in state.order_depths:
                continue

            od = state.order_depths[product]
            pos = state.position.get(product, 0)

            if product == "ASH_COATED_OSMIUM":
                result[product] = trade_osm(od, pos, pstate)
            else:
                result[product] = trade_pepper(od, pos, pstate)

        return result, 0, json.dumps(pstate.to_dict())