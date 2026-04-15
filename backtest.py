"""
Backtest simulator for the strategy – does NOT import datamodel (standalone).
Reads prices CSV and simulates order-fill logic to estimate P&L.
"""
import pandas as pd
import math
import json

# ── Replicated constants ──────────────────────────────────────────────────────
OSM_FAIR_VALUE_INIT = 10_000
OSM_ALPHA           = 0.10
OSM_SPREAD_HALF     = 3
OSM_MAX_ORDER_SIZE  = 10
OSM_INVENTORY_SKEW  = 0.4

PEP_EMA_FAST        = 8
PEP_EMA_SLOW        = 30
PEP_TREND_THRESH    = 15.0
PEP_BUY_DIP_OFFSET  = 5
PEP_BASE_SIZE       = 6
PEP_MAX_LONG_RATIO  = 0.90
PEP_REDUCE_RATIO    = 0.30
PEP_SELL_AGGR       = 2

LIMIT_OSM = 50
LIMIT_PEP = 50


def clamp_qty(qty, pos, limit):
    if qty > 0:
        return min(qty, limit - pos)
    return max(qty, -limit - pos)


def ema_update(prev, new_val, alpha):
    return alpha * new_val + (1 - alpha) * prev


def simulate():
    total_osm_pnl = 0.0
    total_pep_pnl = 0.0
    osm_trades = 0
    pep_trades = 0

    for day in ['-2', '-1', '0']:
        df = pd.read_csv(f'prices_round_1_day_{day}.csv', sep=';')

        # ── OSM ──
        osm_df  = df[df['product'] == 'ASH_COATED_OSMIUM'].copy()
        osm_fv  = float(OSM_FAIR_VALUE_INIT)
        pos_osm = 0
        cash_osm = 0.0

        for _, row in osm_df.iterrows():
            bb = row['bid_price_1']
            ba = row['ask_price_1']
            if pd.isna(bb) or pd.isna(ba):
                continue
            mid = (bb + ba) / 2.0
            osm_fv = ema_update(osm_fv, mid, OSM_ALPHA)
            fv = osm_fv
            skew = OSM_INVENTORY_SKEW * pos_osm

            # Take underpriced asks
            if ba < fv - OSM_SPREAD_HALF:
                qty = clamp_qty(OSM_MAX_ORDER_SIZE, pos_osm, LIMIT_OSM)
                if qty > 0:
                    cash_osm -= qty * ba
                    pos_osm  += qty
                    osm_trades += 1

            # Take overpriced bids
            if bb > fv + OSM_SPREAD_HALF:
                qty = clamp_qty(-OSM_MAX_ORDER_SIZE, pos_osm, LIMIT_OSM)
                if qty < 0:
                    cash_osm -= qty * bb  # selling: cash increases
                    pos_osm  += qty
                    osm_trades += 1

            # Passive quote fills: assume best bid/ask fill at those prices if inside our spread
            my_bid = math.floor(fv - skew - OSM_SPREAD_HALF)
            my_ask = math.ceil(fv - skew + OSM_SPREAD_HALF)

            # Passive bid gets filled if best ask hits it (best ask goes down to our bid)
            # Conservative: assume 30% fill rate for passives
            fill_prob = 0.30
            passive_bid_qty = clamp_qty(OSM_MAX_ORDER_SIZE, pos_osm, LIMIT_OSM)
            if passive_bid_qty > 0 and my_bid < ba:
                filled = max(1, int(passive_bid_qty * fill_prob))
                filled = clamp_qty(filled, pos_osm, LIMIT_OSM)
                if filled > 0:
                    cash_osm -= filled * my_bid
                    pos_osm  += filled

            passive_ask_qty = clamp_qty(-OSM_MAX_ORDER_SIZE, pos_osm, LIMIT_OSM)
            if passive_ask_qty < 0 and my_ask > bb:
                filled = max(1, int(abs(passive_ask_qty) * fill_prob))
                filled = clamp_qty(-filled, pos_osm, LIMIT_OSM)
                if filled < 0:
                    cash_osm -= filled * my_ask
                    pos_osm  += filled

        # Mark to market at last mid
        last_osm_mid = (osm_df['bid_price_1'].iloc[-1] + osm_df['ask_price_1'].iloc[-1]) / 2
        osm_pnl = cash_osm + pos_osm * last_osm_mid
        total_osm_pnl += osm_pnl
        print(f"OSM day={day}: pnl={osm_pnl:.0f} pos={pos_osm} fv={osm_fv:.1f}")

        # ── PEP ──
        pep_df    = df[df['product'] == 'INTARIAN_PEPPER_ROOT'].copy()
        alpha_fast = 2.0 / (PEP_EMA_FAST + 1)
        alpha_slow = 2.0 / (PEP_EMA_SLOW + 1)
        pep_fast  = 0.0
        pep_slow  = 0.0
        pep_init  = False
        pos_pep   = 0
        cash_pep  = 0.0

        for _, row in pep_df.iterrows():
            bb = row['bid_price_1']
            ba = row['ask_price_1']
            if pd.isna(bb) or pd.isna(ba):
                continue
            mid = (bb + ba) / 2.0

            if not pep_init:
                pep_fast = mid
                pep_slow = mid
                pep_init = True
            else:
                pep_fast = ema_update(pep_fast, mid, alpha_fast)
                pep_slow = ema_update(pep_slow, mid, alpha_slow)

            momentum = pep_fast - pep_slow
            bullish  = momentum > PEP_TREND_THRESH

            if bullish:
                target_pos = int(LIMIT_PEP * PEP_MAX_LONG_RATIO)
                needed     = target_pos - pos_pep
                if needed > 0 and ba <= pep_fast + PEP_BUY_DIP_OFFSET:
                    buy_qty = min(needed, PEP_BASE_SIZE)
                    buy_qty = clamp_qty(buy_qty, pos_pep, LIMIT_PEP)
                    if buy_qty > 0:
                        cash_pep -= buy_qty * ba
                        pos_pep  += buy_qty
                        pep_trades += 1
            else:
                target_pos = int(LIMIT_PEP * PEP_REDUCE_RATIO)
                excess     = pos_pep - target_pos
                if excess > 0 and bb is not None and not math.isnan(bb):
                    sell_price = bb - PEP_SELL_AGGR
                    sell_qty   = min(excess, PEP_BASE_SIZE)
                    sell_qty   = clamp_qty(-sell_qty, pos_pep, LIMIT_PEP)
                    if sell_qty < 0:
                        cash_pep -= sell_qty * sell_price
                        pos_pep  += sell_qty
                        pep_trades += 1

        last_pep_mid = (pep_df['bid_price_1'].iloc[-1] + pep_df['ask_price_1'].iloc[-1]) / 2
        pep_pnl = cash_pep + pos_pep * last_pep_mid
        total_pep_pnl += pep_pnl
        print(f"PEP day={day}: pnl={pep_pnl:.0f} pos={pos_pep} fast={pep_fast:.1f} slow={pep_slow:.1f}")

    print()
    print(f"TOTAL OSM P&L: {total_osm_pnl:.0f} ({osm_trades} trades)")
    print(f"TOTAL PEP P&L: {total_pep_pnl:.0f} ({pep_trades} trades)")
    print(f"COMBINED P&L:  {total_osm_pnl + total_pep_pnl:.0f}")


simulate()
