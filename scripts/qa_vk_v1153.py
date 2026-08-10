"""Deterministic 100-scenario smoke audit for the v1.15.3 VK/account merge core."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile

import aiosqlite

from app.config import settings
from app.services.account_identity import _merge_reader_data
from app.services.vk_api import vk_main_keyboard
from app.services.vk_payments import votes_for_stars


SCHEMA = """
CREATE TABLE users(id INTEGER PRIMARY KEY);
CREATE TABLE reading_progress(id INTEGER PRIMARY KEY,user_id INTEGER,chapter_id INTEGER,position_percent INTEGER,updated_at TEXT,UNIQUE(user_id,chapter_id));
CREATE TABLE listening_progress(id INTEGER PRIMARY KEY,user_id INTEGER,audio_chapter_id INTEGER,position_seconds INTEGER,updated_at TEXT,UNIQUE(user_id,audio_chapter_id));
CREATE TABLE graphic_reading_progress(id INTEGER PRIMARY KEY,user_id INTEGER,graphic_chapter_id INTEGER,page_number INTEGER,updated_at TEXT,UNIQUE(user_id,graphic_chapter_id));
CREATE TABLE purchases(id INTEGER PRIMARY KEY,user_id INTEGER,telegram_payment_charge_id TEXT UNIQUE);
CREATE TABLE bookmarks(id INTEGER PRIMARY KEY,user_id INTEGER,book_id INTEGER,UNIQUE(user_id,book_id));
CREATE TABLE reader_wallets(user_id INTEGER PRIMARY KEY,balance_stars INTEGER,created_at TEXT,updated_at TEXT);
CREATE TABLE reader_wallet_transactions(id INTEGER PRIMARY KEY,user_id INTEGER,amount_stars INTEGER);
CREATE TABLE wallet_topups(id INTEGER PRIMARY KEY,user_id INTEGER,telegram_payment_charge_id TEXT UNIQUE);
CREATE TABLE user_achievements(id INTEGER PRIMARY KEY,user_id INTEGER,achievement_code TEXT,progress_value INTEGER,UNIQUE(user_id,achievement_code));
CREATE TABLE author_profiles(id INTEGER PRIMARY KEY,user_id INTEGER UNIQUE);
CREATE TABLE books(id INTEGER PRIMARY KEY,author_id INTEGER);
"""


async def scenario(number: int) -> None:
    handle, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(handle)
    try:
        db = await aiosqlite.connect(path)
        db.row_factory = aiosqlite.Row
        await db.executescript(SCHEMA)
        await db.executemany("INSERT INTO users(id) VALUES(?)", [(1,), (2,)])
        primary_progress = number % 101
        secondary_progress = (number * 7) % 101
        primary_wallet = number % 13
        secondary_wallet = number % 17
        await db.executemany("INSERT INTO reading_progress VALUES(?,?,?,?,?)", [
            (1, 1, 10, primary_progress, "a"), (2, 2, 10, secondary_progress, "b"),
            (3, 2, 100 + number, number % 100, "b"),
        ])
        await db.executemany("INSERT INTO purchases VALUES(?,?,?)", [(1, 1, f"tg:{number}"), (2, 2, f"vk:{number}")])
        await db.executemany("INSERT INTO bookmarks VALUES(?,?,?)", [(1, 1, 7), (2, 2, 7), (3, 2, 100 + number)])
        await db.executemany("INSERT INTO reader_wallets VALUES(?,?,?,?)", [(1, primary_wallet, "a", "a"), (2, secondary_wallet, "b", "b")])
        await db.executemany("INSERT INTO user_achievements VALUES(?,?,?,?)", [(1, 1, "read", number), (2, 2, "read", 100 - number)])
        await _merge_reader_data(db, 2, 1)
        await db.commit()
        progress = await (await db.execute("SELECT position_percent FROM reading_progress WHERE user_id=1 AND chapter_id=10")).fetchone()
        wallet = await (await db.execute("SELECT balance_stars FROM reader_wallets WHERE user_id=1")).fetchone()
        assert int(progress[0]) == max(primary_progress, secondary_progress)
        assert int(wallet[0]) == primary_wallet + secondary_wallet
        assert int((await (await db.execute("SELECT COUNT(*) FROM purchases WHERE user_id=1")).fetchone())[0]) == 2
        assert int((await (await db.execute("SELECT COUNT(*) FROM bookmarks WHERE user_id=1")).fetchone())[0]) == 2
        await db.close()
    finally:
        os.unlink(path)


async def main() -> None:
    for number in range(1, 101):
        await scenario(number)
    settings.VK_APP_ID = 54713417
    settings.VK_GROUP_ID = 240755410
    settings.VK_VOTES_PER_STAR = 0.25
    keyboard = json.loads(vk_main_keyboard())
    assert len(keyboard["buttons"]) == 4
    assert keyboard["buttons"][0][0]["action"]["owner_id"] == -240755410
    assert votes_for_stars(99) == 99
    print("OK: 100 merge scenarios + VK keyboard + no-loss vote floor")


if __name__ == "__main__":
    asyncio.run(main())
