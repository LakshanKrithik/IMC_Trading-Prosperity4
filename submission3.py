from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List
import json, math

PRODUCTS = ["ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT"]
POS_LIMIT = {"ASH_COATED_OSMIUM":50, "INTARIAN_PEPPER_ROOT":50}

# Tunable parameters
OSM_EMA_ALPHA = 0.12    # smoothing for fair-value (EMA)
OSM_TAKE_EDGE = 1       # ticks away from fair to take
OSM_BASE_QTY = 10       # size for Osmium orders

PEP_FAST_ALPHA = 2/(8+1)   # ~8-period EMA
PEP_SLOW_ALPHA = 2/(30+1)  # ~30-period EMA
PEP_TREND_THRESH = 1.5     # EMA trend threshold in ticks
PEP_TAKE_EDGE = 1          # take-profit edge
PEP_BASE_QTY = 8           # base size for Pepper orders
PEP_MAX_TREND_POS = 40     # maximum inventory when trend-trading

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

def clamp(qty, pos, limit):
    # Ensure qty does not breach position limit
    if qty > 0:
        return min(qty, limit - pos)
    else:
        return max(qty, -limit - pos)

class PersistentState:
    def __init__(self):
        self.osm_fv = 10000.0
        self.pep_fast = 0.0
        self.pep_slow = 0.0
        self.pep_init = False
    def to_dict(self):
        return {"osm_fv":self.osm_fv, "pep_fast":self.pep_fast,
                "pep_slow":self.pep_slow, "pep_init":self.pep_init}
    @staticmethod
    def from_dict(d):
        s = PersistentState()
        s.osm_fv  = d.get("osm_fv", 10000.0)
        s.pep_fast = d.get("pep_fast", 0.0)
        s.pep_slow = d.get("pep_slow", 0.0)
        s.pep_init = d.get("pep_init", False)
        return s

def trade_osm(order_depth: OrderDepth, position: int, pstate: PersistentState) -> List[Order]:
    product = "ASH_COATED_OSMIUM"; limit = POS_LIMIT[product]
    orders = []
    bb, bb_vol = best_bid(order_depth); ba, ba_vol = best_ask(order_depth)
    if bb is None or ba is None:
        return orders
    mid = (bb + ba)/2
    # Update fair value (EMA of mid)
    pstate.osm_fv = (1 - OSM_EMA_ALPHA)*pstate.osm_fv + OSM_EMA_ALPHA*mid
    fv = pstate.osm_fv
    # 1) Aggressive takes:
    #   - Buy if ask is significantly below fair (cheap)
    if ba <= fv - OSM_TAKE_EDGE:
        qty = clamp(abs(ba_vol), position, limit)
        qty = min(qty, OSM_BASE_QTY)
        if qty > 0:
            orders.append(Order(product, ba, qty))
            position += qty
    #   - Sell if bid is significantly above fair (rich)
    if bb >= fv + OSM_TAKE_EDGE:
        qty = min(bb_vol, OSM_BASE_QTY, limit + position)
        if qty > 0:
            orders.append(Order(product, bb, -qty))
            position -= qty
    # 2) Passive market making:
    skew = (position / limit) * 1.5
    bid_target = math.floor(fv - skew)
    ask_target = math.ceil(fv + skew)
    buy_price = min(bb + 1, bid_target)
    sell_price = max(ba - 1, ask_target)
    buy_qty = clamp(OSM_BASE_QTY, position, limit)
    sell_qty = clamp(-OSM_BASE_QTY, position, limit)
    if buy_qty > 0 and buy_price < ba:
        orders.append(Order(product, buy_price, buy_qty))
    if sell_qty < 0 and sell_price > bb:
        orders.append(Order(product, sell_price, sell_qty))
    return orders

def trade_pepper(order_depth: OrderDepth, position: int, pstate: PersistentState) -> List[Order]:
    product = "INTARIAN_PEPPER_ROOT"; limit = POS_LIMIT[product]
    orders = []
    bb, bb_vol = best_bid(order_depth); ba, ba_vol = best_ask(order_depth)
    if bb is None or ba is None:
        return orders
    mid = (bb + ba)/2
    # Initialize EMAs
    if not pstate.pep_init:
        pstate.pep_fast = mid; pstate.pep_slow = mid; pstate.pep_init = True
    else:
        pstate.pep_fast = (1 - PEP_FAST_ALPHA)*pstate.pep_fast + PEP_FAST_ALPHA*mid
        pstate.pep_slow = (1 - PEP_SLOW_ALPHA)*pstate.pep_slow + PEP_SLOW_ALPHA*mid
    fast = pstate.pep_fast; slow = pstate.pep_slow; trend = fast - slow

    # Trend up: bias long (buy dips, smaller sell)
    if trend > PEP_TREND_THRESH:
        if ba <= fast + PEP_TAKE_EDGE and position < PEP_MAX_TREND_POS:
            qty = min(abs(ba_vol), PEP_BASE_QTY, limit - position)
            if qty > 0:
                orders.append(Order(product, ba, qty))
                position += qty
        buy_price = min(bb + 1, int(math.floor(fast)))
        buy_qty = min(PEP_BASE_QTY, limit - position)
        if buy_qty > 0 and buy_price < ba:
            orders.append(Order(product, buy_price, buy_qty))
        # Trim if already very long
        if position > 0.8 * limit:
            sell_qty = min(bb_vol, int(position - 0.6*limit))
            if sell_qty > 0:
                orders.append(Order(product, bb, -sell_qty))

    # Trend down: bias short (sell spikes, smaller buy)
    elif trend < -PEP_TREND_THRESH:
        if bb >= fast - PEP_TAKE_EDGE and position > -PEP_MAX_TREND_POS:
            qty = min(bb_vol, PEP_BASE_QTY, limit + position)
            if qty > 0:
                orders.append(Order(product, bb, -qty))
                position -= qty
        sell_price = max(ba - 1, int(math.ceil(fast)))
        sell_qty = min(PEP_BASE_QTY, limit + position)
        if sell_qty > 0 and sell_price > bb:
            orders.append(Order(product, sell_price, -sell_qty))
        if position < -0.8 * limit:
            buy_qty = min(abs(ba_vol), int(-0.6*limit) - position)
            if buy_qty > 0:
                orders.append(Order(product, ba, buy_qty))

    # Neutral trend: symmetric market making
    else:
        buy_price = bb + 1; sell_price = ba - 1
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
        except json.JSONDecodeError:
            pstate = PersistentState()
        result: Dict[str, List[Order]] = {}
        for product in PRODUCTS:
            if product not in state.order_depths:
                continue
            od = state.order_depths[product]
            pos = state.position.get(product, 0)
            if product == "ASH_COATED_OSMIUM":
                result[product] = trade_osm(od, pos, pstate)
            else:  # INTARIAN_PEPPER_ROOT
                result[product] = trade_pepper(od, pos, pstate)
        trader_data = json.dumps(pstate.to_dict())
        return result, 0, trader_data
