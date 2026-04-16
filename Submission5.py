from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List
import json

PRODUCTS = ["ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT"]
POS_LIMIT = {"ASH_COATED_OSMIUM": 50, "INTARIAN_PEPPER_ROOT": 50}

# Tunable parameters
OSM_EMA_ALPHA = 0.1    # EMA smoothing for Osmium
OSM_TAKE_EDGE = 1      # ticks away from fair value to aggressively trade
OSM_BASE_QTY = 20      # size for aggressive orders

PEP_FAST_ALPHA = 2/6   # fast EMA alpha (~5-step EMA)
PEP_SLOW_ALPHA = 2/16  # slow EMA alpha (~15-step EMA)
PEP_TREND_THRESH = 0.5 # EMA diff threshold to consider a trend
PEP_TAKE_EDGE = 2      # additional ticks to take in trend
PEP_BASE_QTY = 15      # base size for aggressive Pep orders

class PersistentState:
    def __init__(self):
        self.osm_fv = 10000.0    # Osmium fair value estimate
        self.pep_fast = None     # Pepper fast EMA
        self.pep_slow = None     # Pepper slow EMA

    def to_dict(self):
        return {"osm_fv": self.osm_fv, "pep_fast": self.pep_fast, "pep_slow": self.pep_slow}
    @staticmethod
    def from_dict(d):
        p = PersistentState()
        p.osm_fv = d.get("osm_fv", 10000.0)
        p.pep_fast = d.get("pep_fast", None)
        p.pep_slow = d.get("pep_slow", None)
        return p

def best_bid(od: OrderDepth):
    if od.buy_orders:
        price = max(od.buy_orders.keys())
        return price, od.buy_orders[price]
    return None, 0

def best_ask(od: OrderDepth):
    if od.sell_orders:
        price = min(od.sell_orders.keys())
        return price, od.sell_orders[price]
    return None, 0

def trade_osm(order_depth, pos, state: PersistentState):
    """ASH_COATED_OSMIUM: EMA fair-value + skewed market making + spread-taking."""
    product = "ASH_COATED_OSMIUM"
    limit = POS_LIMIT[product]
    bb, bb_vol = best_bid(order_depth)
    ba, ba_vol = best_ask(order_depth)
    if bb is None or ba is None:
        return []
    mid = (bb + ba) / 2
    # Update fair-value EMA
    state.osm_fv = (1 - OSM_EMA_ALPHA) * state.osm_fv + OSM_EMA_ALPHA * mid
    fair = state.osm_fv

    orders: List[Order] = []
    # 1) TAKE: buy if ask is cheap relative to fair, sell if bid is rich.
    if ba <= fair - OSM_TAKE_EDGE:
        qty = min(ba_vol, limit - pos, OSM_BASE_QTY)
        if qty > 0:
            orders.append(Order(product, ba, qty))
            pos += qty
    if bb >= fair + OSM_TAKE_EDGE:
        qty = min(bb_vol, limit + pos, OSM_BASE_QTY)
        if qty > 0:
            orders.append(Order(product, bb, -qty))
            pos -= qty

    # 2) MARKET MAKE with inventory skew
    skew = (pos / limit) * 1.5
    buy_px = min(bb + 1, int(fair - skew))
    sell_px = max(ba - 1, int(fair + skew))
    buy_qty = min(OSM_BASE_QTY, limit - pos)
    sell_qty = min(OSM_BASE_QTY, limit + pos)
    if buy_qty > 0 and buy_px < ba:
        orders.append(Order(product, buy_px, buy_qty))
    if sell_qty > 0 and sell_px > bb:
        orders.append(Order(product, sell_px, -sell_qty))

    # 3) Inventory control: exit if position too large
    if pos > 30:
        # sell excess
        orders.append(Order(product, bb, -min(pos-30, 10)))
    if pos < -30:
        # buy back if short
        orders.append(Order(product, ba, min(-pos-30, 10)))

    return orders

def trade_pepper(order_depth, pos, state: PersistentState):
    """INTARIAN_PEPPER_ROOT: Trend-biased spread capture + quoting."""
    product = "INTARIAN_PEPPER_ROOT"
    limit = POS_LIMIT[product]
    bb, bb_vol = best_bid(order_depth)
    ba, ba_vol = best_ask(order_depth)
    if bb is None or ba is None:
        return []
    mid = (bb + ba) / 2

    # Initialize/update dual EMAs for trend detection
    if state.pep_fast is None:
        state.pep_fast = mid
        state.pep_slow = mid
    else:
        state.pep_fast = (1 - PEP_FAST_ALPHA) * state.pep_fast + PEP_FAST_ALPHA * mid
        state.pep_slow = (1 - PEP_SLOW_ALPHA) * state.pep_slow + PEP_SLOW_ALPHA * mid

    fast = state.pep_fast
    slow = state.pep_slow
    trend = fast - slow

    # Determine how aggressively to take each side
    if trend > PEP_TREND_THRESH:
        # Uptrend: buy dips more aggressively (wider take range), sell less
        buy_edge = PEP_TAKE_EDGE + 2
        sell_edge = PEP_TAKE_EDGE
    elif trend < -PEP_TREND_THRESH:
        # Downtrend: sell pops more aggressively
        buy_edge = PEP_TAKE_EDGE
        sell_edge = PEP_TAKE_EDGE + 2
    else:
        # Neutral trend
        buy_edge = PEP_TAKE_EDGE
        sell_edge = PEP_TAKE_EDGE

    orders: List[Order] = []
    # 1) TAKE spread: buy cheap asks, sell rich bids
    for ask, vol in sorted(order_depth.sell_orders.items()):
        if ask <= mid + buy_edge:
            qty = min(vol, limit - pos, PEP_BASE_QTY)
            if qty > 0:
                orders.append(Order(product, ask, qty))
                pos += qty
        else:
            break
    for bid, vol in sorted(order_depth.buy_orders.items(), reverse=True):
        if bid >= mid - sell_edge:
            qty = min(vol, limit + pos, PEP_BASE_QTY)
            if qty > 0:
                orders.append(Order(product, bid, -qty))
                pos -= qty
        else:
            break

    # 2) Passive quoting with slight skew
    inv_skew = (pos / limit) * 1.2
    buy_px = min(bb + 1, int(mid - inv_skew))
    sell_px = max(ba - 1, int(mid + inv_skew))
    buy_qty = min(PEP_BASE_QTY, limit - pos)
    sell_qty = min(PEP_BASE_QTY, limit + pos)
    # If strongly trending, limit the opposite side
    if trend > PEP_TREND_THRESH:
        sell_qty = min(sell_qty, 5)
    elif trend < -PEP_TREND_THRESH:
        buy_qty = min(buy_qty, 5)
    if buy_qty > 0 and buy_px < ba:
        orders.append(Order(product, buy_px, buy_qty))
    if sell_qty > 0 and sell_px > bb:
        orders.append(Order(product, sell_px, -sell_qty))

    # 3) Inventory control
    if pos > 30:
        orders.append(Order(product, bb, -min(pos-30, 10)))
    if pos < -30:
        orders.append(Order(product, ba, min(-pos-30, 10)))

    return orders

class Trader:
    def run(self, state: TradingState):
        # Load/save persistent EMAs
        try:
            pstate = PersistentState.from_dict(json.loads(state.traderData)) if state.traderData else PersistentState()
        except:
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

        newdata = json.dumps(pstate.to_dict())
        return result, 0, newdata
