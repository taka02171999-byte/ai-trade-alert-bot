def suggest_break_range(symbol: str):
    return {
        "entry_break_pct_low": 0.8,
        "entry_break_pct_high": 1.0,
        "comment": "AI想定レンジ: ブレイク0.8〜1.0%"
    }

def is_symbol_currently_ok(symbol: str) -> bool:
    return True
