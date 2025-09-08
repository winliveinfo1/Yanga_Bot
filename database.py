import aiosqlite

DB_FILE = "economy.db"

async def execute(query, params=()):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(query, params)
        await db.commit()

async def fetchone(query, params=()):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(query, params) as cursor:
            return await cursor.fetchone()

async def fetchall(query, params=()):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute(query, params) as cursor:
            return await cursor.fetchall()

# Inicializar tabla
async def init_db():
    await execute("""
        CREATE TABLE IF NOT EXISTS balances (
            user_id INTEGER PRIMARY KEY,
            balance INTEGER NOT NULL
        )
    """)
