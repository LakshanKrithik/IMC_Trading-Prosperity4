
import pandas as pd
import numpy as np

print("=== TRADES ANALYSIS ===\n")
for day in ['-2', '-1', '0']:
    fname = f'trades_round_1_day_{day}.csv'
    df = pd.read_csv(fname, sep=';')
    print(f"--- {fname} ---")
    print("Columns:", df.columns.tolist())
    print(df.head(3).to_string())
    print()
    for sym in df['symbol'].dropna().unique():
        sub = df[df['symbol'] == sym]
        print(f"  {sym}: {len(sub)} trades, price range [{sub['price'].min():.0f}, {sub['price'].max():.0f}], avg={sub['price'].mean():.1f}")
    print()

print("\n=== PRICES ANALYSIS ===\n")
for day in ['-2', '-1', '0']:
    fname = f'prices_round_1_day_{day}.csv'
    df = pd.read_csv(fname, sep=';')
    print(f"--- {fname} ---")
    print("Columns:", df.columns.tolist())
    print(df.head(3).to_string())
    print()
    for sym in df['product'].dropna().unique():
        sub = df[df['product'] == sym]
        mids = (sub['bid_price_1'] + sub['ask_price_1']) / 2
        print(f"  {sym}: {len(sub)} ticks, mid range [{mids.min():.0f}, {mids.max():.0f}], avg mid={mids.mean():.1f}")
    print()
