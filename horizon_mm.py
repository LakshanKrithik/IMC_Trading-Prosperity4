from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List


PRODUCTS = ["ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT"]

POS_LIMIT = {
    "ASH_COATED_OSMIUM": 50,
    "INTARIAN_PEPPER_ROOT": 50,
}


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


def buy_capacity(position: int, limit: int) -> int:
    return max(0, limit - position)


def sell_capacity(position: int, limit: int) -> int:
    return max(0, limit + position)


def take_liquidity(product: str, order_depth: OrderDepth, position: int, adj_fair: float) -> List[Order]:
    orders: List[Order] = []
    limit = POS_LIMIT[product]

    bb, bb_vol = best_bid(order_depth)
    ba, ba_vol = best_ask(order_depth)

    if bb is None or ba is None:
        return orders

    # Take cheap asks
    if ba <= adj_fair - 1:
        qty = min(abs(ba_vol), buy_capacity(position, limit))
        if qty > 0:
            orders.append(Order(product, ba, qty))
            position += qty

    # Take rich bids
    if bb >= adj_fair + 1:
        qty = min(bb_vol, sell_capacity(position, limit))
        if qty > 0:
            orders.append(Order(product, bb, -qty))
            position -= qty

    return orders


def market_make(product: str, order_depth: OrderDepth, position: int, adj_fair: float) -> List[Order]:
    orders: List[Order] = []
    limit = POS_LIMIT[product]

    bb, _ = best_bid(order_depth)
    ba, _ = best_ask(order_depth)

    if bb is None or ba is None:
        return orders

    spread = ba - bb
    if spread <= 0:
        return orders

    edge = 1
    quote_size = 10 if product == "ASH_COATED_OSMIUM" else 8

    # We use int() nicely rounded with floating adj_fair
    buy_price = min(bb + edge, int(adj_fair - 0.5))
    sell_price = max(ba - edge, int(adj_fair + 0.5))

    if buy_price >= ba:
        buy_price = bb
    if sell_price <= bb:
        sell_price = ba

    bq = min(quote_size, buy_capacity(position, limit))
    sq = min(quote_size, sell_capacity(position, limit))

    if bq > 0:
        orders.append(Order(product, buy_price, bq))

    if sq > 0:
        orders.append(Order(product, sell_price, -sq))

    return orders


def trade_osm(order_depth: OrderDepth, position: int) -> List[Order]:
    orders = []
    fair = mid_price(order_depth)
    if fair is None: return orders
    # Max skew of 2 ticks at position=50
    adj_fair = fair - (position / 50.0) * 1.5 
    orders.extend(take_liquidity("ASH_COATED_OSMIUM", order_depth, position, adj_fair))
    position = position + sum(o.quantity for o in orders)
    orders.extend(market_make("ASH_COATED_OSMIUM", order_depth, position, adj_fair))
    return orders


def trade_pepper(order_depth: OrderDepth, position: int) -> List[Order]:
    orders = []
    fair = mid_price(order_depth)
    if fair is None: return orders
    # Max skew of 3 ticks for pepper
    adj_fair = fair - (position / 50.0) * 2.0
    orders.extend(take_liquidity("INTARIAN_PEPPER_ROOT", order_depth, position, adj_fair))
    position = position + sum(o.quantity for o in orders)
    orders.extend(market_make("INTARIAN_PEPPER_ROOT", order_depth, position, adj_fair))
    return orders


class Trader:
    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}

        for product in PRODUCTS:
            if product not in state.order_depths:
                continue

            order_depth = state.order_depths[product]
            position = state.position.get(product, 0)

            if product == "ASH_COATED_OSMIUM":
                result[product] = trade_osm(order_depth, position)
            elif product == "INTARIAN_PEPPER_ROOT":
                result[product] = trade_pepper(order_depth, position)

        return result, 0, ""