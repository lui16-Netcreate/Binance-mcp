import sqlite3
import json
from pathlib import Path

DB_PATH = Path(__file__).parent / "signals.db"


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol       TEXT,
                action       TEXT,
                price        REAL,
                indicator    TEXT,
                timeframe    TEXT,
                message      TEXT,
                raw          TEXT,
                received_at  TEXT DEFAULT (datetime('now'))
            )
        """)


def insert_signal(data: dict):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO signals (symbol, action, price, indicator, timeframe, message, raw)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.get("symbol"),
                data.get("action"),
                data.get("price"),
                data.get("indicator"),
                data.get("timeframe"),
                data.get("message"),
                json.dumps(data),
            ),
        )


def get_signals(symbol: str = None, action: str = None, limit: int = 20) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        query = "SELECT * FROM signals WHERE 1=1"
        params: list = []
        if symbol:
            query += " AND UPPER(symbol) = ?"
            params.append(symbol.upper())
        if action:
            query += " AND UPPER(action) = ?"
            params.append(action.upper())
        query += " ORDER BY received_at DESC LIMIT ?"
        params.append(limit)
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def clear_signals():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM signals")
