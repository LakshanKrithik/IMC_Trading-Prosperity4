import numpy as np

# State: track current positions and last mid-prices
position = {"ASH_COATED_OSMIUM": 0, "INTARIAN_PEPPER_ROOT": 0}
last_mid = {"ASH_COATED_OSMIUM": None, "INTARIAN_PEPPER_ROOT": None}
# Define strategy parameters
BUY_THRESHOLD = 5     # threshold to trigger momentum trade on PEP
MAX_POS = 40          # we avoid holding >40 units to limit risk
BASE_VOL_OSM = 5      # base limit order size for OSM
BASE_VOL_PEP = 10     # base order size for PEP

# (In the actual trading system, the below would be inside the market-data callback)
def on_order_book_update(timestamp, order_book):
    """
    Called each tick with the latest order book snapshot for both products.
    order_book is assumed to have fields:
      order_book[product].bid_price, ask_price, bid_size, ask_size, mid_price.
    """
    global position, last_mid
    
    # Process each product
    for product in ["ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT"]:
        data = order_book[product]
        bid = data.bid_price
        ask = data.ask_price
        mid = data.mid_price
        # Fallback if mid not provided
        if mid is None and bid is not None and ask is not None:
            mid = (bid + ask) / 2.0
        
        # Initialize last_mid if needed
        if last_mid[product] is None:
            last_mid[product] = mid
        
        # Strategy for Ash-coated Osmium: pure spread capture
        if product == "ASH_COATED_OSMIUM":
            # Place limit buy at bid, sell at ask to capture spread
            if bid is not None and position[product] < MAX_POS:
                # buy up to BASE_VOL_OSM or until position limit
                order_size = min(BASE_VOL_OSM, MAX_POS - position[product])
                place_limit_buy(product, price=bid, size=order_size)
            if ask is not None and position[product] > -MAX_POS:
                order_size = min(BASE_VOL_OSM, position[product] + MAX_POS)
                place_limit_sell(product, price=ask, size=order_size)
        
        # Strategy for Intarian Pepper Root: spread capture + momentum
        else:  # INTARIAN_PEPPER_ROOT
            # Calculate momentum
            change = mid - last_mid[product]
            # If price is rising strongly and we have room, buy aggressively
            if change > BUY_THRESHOLD and position[product] < MAX_POS:
                # Only buy if we haven't built up a large long already
                if position[product] < MAX_POS:
                    buy_size = min(BASE_VOL_PEP, MAX_POS - position[product])
                    # Place a market order to buy at current ask
                    if ask is not None:
                        place_market_buy(product, price=ask, size=buy_size)
                        position[product] += buy_size  # update position
            # Regardless, continue capturing spread with limits
            if bid is not None and position[product] < MAX_POS:
                buy_size = min(BASE_VOL_PEP, MAX_POS - position[product])
                place_limit_buy(product, price=bid, size=buy_size)
            if ask is not None and position[product] > -MAX_POS:
                sell_size = min(BASE_VOL_PEP, position[product] + MAX_POS)
                place_limit_sell(product, price=ask, size=sell_size)
        
        # Update last_mid after trading logic
        last_mid[product] = mid

# Helper stubs for order placement (the actual trading API would provide these)
def place_limit_buy(product, price, size):
    """
    Place a limit buy order on `product` at `price` for `size` units.
    """
    pass

def place_limit_sell(product, price, size):
    """
    Place a limit sell order on `product` at `price` for `size` units.
    """
    pass

def place_market_buy(product, price, size):
    """
    Immediately buy `size` units of `product` at the current market price (`price`).
    """
    pass

def place_market_sell(product, price, size):
    """
    Immediately sell `size` units of `product` at the current market price (`price`).
    """
    pass

# End of trading logic code
