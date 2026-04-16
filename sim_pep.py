"""
PEP backtester — simulates submission4.py's trade_pepper() directly.
Mocks the datamodel classes so no IMC package is needed.
"""
import pandas as pd
import math

# ── Mock datamodel ────────────────────────────────────────────────────────────
class OrderDepth:
    def __init__(self, buy_orders, sell_orders):
        self.buy_orders = buy_orders    # {price: qty}  positive qty
        self.sell_orders = sell_orders  # {price: qty}  negative qty

class Order:
    def __init__(self, symbol, price, quantity):
        self.symbol = symbol
        self.price = price
        self.quantity = quantity

# ── Paste submission4 parameters exactly ─────────────────────────────────────
POS_LIMIT = {"ASH_COATED_OSMIUM": 50, "INTARIAN_PEPPER_ROOT": 50}

PEP_FAST_ALPHA = 2 / (8 + 1)
PEP_SLOW_ALPHA = 2 / (30 + 1)
PEP_TREND_THRESH = 1.5
PEP_TAKE_EDGE = 1
PEP_BASE_QTY = 12
PEP_MAX_TREND_POS = 40


def clamp_buy(qty, pos, limit):
    return max(0, min(qty, limit - pos))

def clamp_sell(qty, pos, limit):
    return max(0, min(qty, limit + pos))


class PersistentState:
    def __init__(self):
        self.pep_fast = 0.0
        self.pep_slow = 0.0
        self.pep_init = False


def trade_pepper(order_depth: OrderDepth, position: int, pstate: PersistentState):
    product = "INTARIAN_PEPPER_ROOT"
    limit = POS_LIMIT[product]
    bb_orders = order_depth.buy_orders
    sa_orders = order_depth.sell_orders

    if not bb_orders or not sa_orders:
        return []

    bb = max(bb_orders.keys())
    bb_vol = bb_orders[bb]
    ba = min(sa_orders.keys())
    ba_vol = sa_orders[ba]
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

    orders = []

    # Take cheap asks
    for ask, vol in sorted(sa_orders.items()):
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
    for bid, vol in sorted(bb_orders.items(), reverse=True):
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


# ── Simulate passive fill ─────────────────────────────────────────────────────
PASSIVE_FILL_RATE = 0.15  # conservative fill rate for maker orders

def simulate_fills(orders, row, position, cash, taker_trades, passive_trades):
    bb = row['bid_price_1']
    ba = row['ask_price_1']
    for o in orders:
        qty = o.quantity
        price = o.price
        if qty > 0:  # buying
            if price >= ba:  # taker (crossed spread)
                cash -= qty * price
                position += qty
                taker_trades += 1
            elif price > bb:  # passive inside spread — assume partial fill
                filled = max(1, int(qty * PASSIVE_FILL_RATE))
                filled = min(filled, 50 - position)
                if filled > 0:
                    cash -= filled * price
                    position += filled
                    passive_trades += 1
        elif qty < 0:  # selling
            qty = abs(qty)
            if price <= bb:  # taker
                cash += qty * price
                position -= qty
                taker_trades += 1
            elif price < ba:  # passive
                filled = max(1, int(qty * PASSIVE_FILL_RATE))
                filled = min(filled, 50 + position)
                if filled > 0:
                    cash += filled * price
                    position -= filled
                    passive_trades += 1
    return position, cash, taker_trades, passive_trades


# ── Main simulation loop ──────────────────────────────────────────────────────
def run():
    total_pep_pnl = 0.0
    total_taker = 0
    total_passive = 0

    for day in ['-2', '-1', '0']:
        df = pd.read_csv(f'prices_round_1_day_{day}.csv', sep=';')
        pep_df = df[df['product'] == 'INTARIAN_PEPPER_ROOT'].copy()

        pos = 0
        cash = 0.0
        pstate = PersistentState()
        taker_trades = 0
        passive_trades = 0

        for _, row in pep_df.iterrows():
            bb = row.get('bid_price_1')
            ba = row.get('ask_price_1')
            if pd.isna(bb) or pd.isna(ba):
                continue

            bb_vol = int(row.get('bid_volume_1', 10) or 10)
            ba_vol = int(row.get('ask_volume_1', 10) or 10)

            od = OrderDepth(
                buy_orders={int(bb): bb_vol},
                sell_orders={int(ba): -ba_vol}
            )

            orders = trade_pepper(od, pos, pstate)
            pos, cash, taker_trades, passive_trades = simulate_fills(orders, row, pos, cash, taker_trades, passive_trades)

        total_taker += taker_trades
        total_passive += passive_trades
        last_mid = (pep_df['bid_price_1'].iloc[-1] + pep_df['ask_price_1'].iloc[-1]) / 2
        day_pnl = cash + pos * last_mid
        total_pep_pnl += day_pnl
        print(f"PEP day={day}: pnl={day_pnl:.0f}  pos={pos}  trend={pstate.pep_fast - pstate.pep_slow:.2f}  taker={taker_trades}  passive={passive_trades}")

    print()
    print(f"TOTAL PEP P&L : {total_pep_pnl:.0f}")
    print(f"Taker trades  : {total_taker}")
    print(f"Passive fills : {total_passive}  (at {PASSIVE_FILL_RATE*100:.0f}% assumed fill rate)")


run()
