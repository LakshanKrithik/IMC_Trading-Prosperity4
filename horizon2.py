from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List
import json
import math

PRODUCTS = ["ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT"]

POS_LIMIT = {
    "ASH_COATED_OSMIUM": 50,
    "INTARIAN_PEPPER_ROOT": 50,
}

# Tunables
OSM_EMA_ALPHA = 0.12
OSM_TAKE_EDGE = 1
OSM_BASE_QTY = 10

PEP_FAST_ALPHA = 2 / (8 + 1)
PEP_SLOW_ALPHA = 2 / (30 + 1)
PEP_TREND_THRESH = 1.5
PEP_TAKE_EDGE = 1
PEP_BASE_QTY = 8
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


def mid_price(order_depth: OrderDepth):
    bb, _ = best_bid(order_depth)
    ba, _ = best_ask(order_depth)
    if bb is not None and ba is not None:
        return (bb + ba) / 2
    if bb is not None:
        return float(bb)
    if ba is not None:
        return float(ba)
    return None


def clamp_buy(qty: int, position: int, limit: int) -> int:
    return max(0, min(qty, limit - position))


def clamp_sell(qty: int, position: int, limit: int) -> int:
    return max(0, min(qty, limit + position))


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


def trade_osm(order_depth: OrderDepth, position: int, pstate: PersistentState) -> List[Order]:
    product = "ASH_COATED_OSMIUM"
    limit = POS_LIMIT[product]
    orders: List[Order] = []

    bb, bb_vol = best_bid(order_depth)
    ba, ba_vol = best_ask(order_depth)
    if bb is None or ba is None:
        return orders

    mid = (bb + ba) / 2
    pstate.osm_fv = (1 - OSM_EMA_ALPHA) * pstate.osm_fv + OSM_EMA_ALPHA * mid
    fv = pstate.osm_fv

    # Take clear mispricings first
    if ba <= fv - OSM_TAKE_EDGE:
        qty = min(abs(ba_vol), clamp_buy(OSM_BASE_QTY, position, limit))
        if qty > 0:
            orders.append(Order(product, ba, qty))
            position += qty

    if bb >= fv + OSM_TAKE_EDGE:
        qty = min(bb_vol, clamp_sell(OSM_BASE_QTY, position, limit))
        if qty > 0:
            orders.append(Order(product, bb, -qty))
            position -= qty

    # Inventory-aware market making
    skew = (position / limit) * 1.5
    bid_target = int(math.floor(fv - skew))
    ask_target = int(math.ceil(fv + skew))

    buy_price = min(bb + 1, bid_target)
    sell_price = max(ba - 1, ask_target)

    buy_qty = clamp_buy(OSM_BASE_QTY, position, limit)
    sell_qty = clamp_sell(OSM_BASE_QTY, position, limit)

    if buy_qty > 0 and buy_price < ba:
        orders.append(Order(product, buy_price, buy_qty))

    if sell_qty > 0 and sell_price > bb:
        orders.append(Order(product, sell_price, -sell_qty))

    return orders


def trade_pepper(order_depth: OrderDepth, position: int, pstate: PersistentState) -> List[Order]:
    product = "INTARIAN_PEPPER_ROOT"
    limit = POS_LIMIT[product]
    orders: List[Order] = []

    bb, bb_vol = best_bid(order_depth)
    ba, ba_vol = best_ask(order_depth)
    if bb is None or ba is None:
        return orders

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

    # Strong uptrend: bias long, buy dips, reduce selling
    if trend > PEP_TREND_THRESH:
        if ba <= fast + PEP_TAKE_EDGE and position < PEP_MAX_TREND_POS:
            qty = min(abs(ba_vol), limit - position, PEP_BASE_QTY)
            if qty > 0:
                orders.append(Order(product, ba, qty))
                position += qty

        buy_price = min(bb + 1, int(math.floor(fast)))
        buy_qty = min(PEP_BASE_QTY, limit - position)
        if buy_qty > 0 and buy_price < ba:
            orders.append(Order(product, buy_price, buy_qty))

        if position > int(0.8 * limit):
            sell_qty = min(bb_vol, position - int(0.6 * limit))
            if sell_qty > 0:
                orders.append(Order(product, bb, -sell_qty))

    # Strong downtrend: bias short, sell pops, reduce buying
    elif trend < -PEP_TREND_THRESH:
        if bb >= fast - PEP_TAKE_EDGE and position > -PEP_MAX_TREND_POS:
            qty = min(bb_vol, limit + position, PEP_BASE_QTY)
            if qty > 0:
                orders.append(Order(product, bb, -qty))
                position -= qty

        sell_price = max(ba - 1, int(math.ceil(fast)))
        sell_qty = min(PEP_BASE_QTY, limit + position)
        if sell_qty > 0 and sell_price > bb:
            orders.append(Order(product, sell_price, -sell_qty))

        if position < int(-0.8 * limit):
            buy_qty = min(abs(ba_vol), int(-0.6 * limit) - position)
            if buy_qty > 0:
                orders.append(Order(product, ba, buy_qty))

    # Neutral: simple market making so the product is never idle
    else:
        buy_price = bb + 1
        sell_price = ba - 1

        buy_qty = min(6, limit - position)
        sell_qty = min(6, limit + position)

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
            elif product == "INTARIAN_PEPPER_ROOT":
                result[product] = trade_pepper(od, pos, pstate)

        trader_data = json.dumps(pstate.to_dict())
        return result, 0, trader_data