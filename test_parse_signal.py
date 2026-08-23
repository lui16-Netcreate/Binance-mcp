"""
Regression check for trader.parse_signal().

Run after any change to the signal parser:
    python3 test_parse_signal.py

Each case is (message_text, expected_fields) where expected_fields is a
dict of the keys we care about checking (not every key needs to be present).
"""

from trader import parse_signal

CASES = [
    (
        "range signal (dash between two prices)",
        "$ETH - I like 2,352 - 2,328 for a LIMIT LONG.\n\n"
        "TP1: 2,376\nTP2: 2,399.50\nTP3: 2,423\nTP4: 2,446\n\n"
        "SL: 2,248 or manual",
        {
            "symbol": "ETHUSDT",
            "direction": "LONG",
            "entries": [2352.0, 2340.0, 2328.0],
            "sl": 2248.0,
            "tps": {"tp1": 2376.0, "tp2": 2399.50, "tp3": 2423.0, "tp4": 2446.0},
        },
    ),
    (
        "single price, number after direction word",
        "$HIGH - I am trying a LONG here 0.2082\n\nSL: 0.1980",
        {
            "symbol": "HIGHUSDT",
            "direction": "LONG",
            "entries": [0.2082],
            "sl": 0.1980,
            "needs_confirm": True,
        },
    ),
    (
        "single price, number BEFORE direction word",
        "$BTC - I like 74,525 for a LIMIT LONG.\n\n"
        "My first targets would be:\n\n"
        "TP1: 75,271\nTP2: 76,016\nTP3: 76,761\nTP4: 78,252\n\n"
        "SL: 71,029",
        {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entries": [74525.0],
            "sl": 71029.0,
            "tps": {"tp1": 75271.0, "tp2": 76016.0, "tp3": 76761.0, "tp4": 78252.0},
            "needs_confirm": True,
        },
    ),
    (
        "no symbol -> should not parse",
        "I like 74,525 for a LIMIT LONG. SL: 71,029",
        None,
    ),
    (
        "no direction -> should not parse",
        "$BTC - I like 74,525. SL: 71,029",
        None,
    ),
]


def run():
    failures = 0
    for name, text, expected in CASES:
        result = parse_signal(text)

        if expected is None:
            ok = result is None
            print(f"{'PASS' if ok else 'FAIL'} - {name}")
            if not ok:
                failures += 1
                print(f"    expected None, got: {result}")
            continue

        if result is None:
            print(f"FAIL - {name}")
            print(f"    expected {expected}, got None")
            failures += 1
            continue

        mismatches = {
            k: (v, result.get(k))
            for k, v in expected.items()
            if result.get(k) != v
        }
        ok = not mismatches
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if mismatches:
            failures += 1
            for k, (want, got) in mismatches.items():
                print(f"    {k}: expected {want!r}, got {got!r}")

    print(f"\n{len(CASES) - failures}/{len(CASES)} passed")
    return failures == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if run() else 1)
