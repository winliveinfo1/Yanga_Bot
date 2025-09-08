from database import execute, fetchone, fetchall, init_db

async def give_money(user_id: int, amount: int):
    row = await fetchone("SELECT balance FROM balances WHERE user_id=?", (user_id,))
    if row:
        await execute("UPDATE balances SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    else:
        await execute("INSERT INTO balances (user_id, balance) VALUES (?, ?)", (user_id, amount))

async def remove_money(user_id: int, amount: int):
    row = await fetchone("SELECT balance FROM balances WHERE user_id=?", (user_id,))
    if row:
        new_balance = max(0, row[0] - amount)
        await execute("UPDATE balances SET balance = ? WHERE user_id=?", (new_balance, user_id))

async def transfer_money(sender_id: int, receiver_id: int, amount: int):
    sender_balance = await get_balance_user(sender_id)
    if sender_balance < amount:
        return False
    await remove_money(sender_id, amount)
    await give_money(receiver_id, amount)
    return True

async def get_balance_user(user_id: int):
    row = await fetchone("SELECT balance FROM balances WHERE user_id=?", (user_id,))
    return row[0] if row else 0

async def get_top_users(limit: int = 10):
    return await fetchall("SELECT user_id, balance FROM balances ORDER BY balance DESC LIMIT ?", (limit,))
