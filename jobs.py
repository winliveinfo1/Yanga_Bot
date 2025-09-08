import random
from economy import give_money

async def do_job(user_id: int, job: str) -> str:
    if job == "pescador":
        amount = random.randint(10, 25)
        bonus = random.choice([0, 5, 10])
        total = amount + bonus
        await give_money(user_id, total)
        return f":fishing_pole_and_fish: Has pescado y ganado {total} Y$ (incluye {bonus} Y$ de bonificación)."

    elif job == "talador":
        fail_chance = random.randint(1, 100)
        if fail_chance <= 20:
            return ":axe: Estabas cansado y no pudiste talar nada. No ganaste Y$."
        amount = random.randint(20, 35)
        await give_money(user_id, amount)
        return f":axe: Has talado árboles y ganado {amount} Y$."

    elif job == "minero":
        amount = random.randint(30, 50)
        await give_money(user_id, amount)
        return f":pick: Has minado minerales y ganado {amount} Y$."

    return ":x: Trabajo no válido." 