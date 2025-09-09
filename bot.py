import discord
from discord import app_commands
from discord.ext import commands
import json
import aiohttp
from typing import Literal
from jobs import do_job
import economy
import time
import random
import os
from datetime import datetime

# -------------------------------
# Configuración
# -------------------------------
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
ADMIN_ROLE_ID = os.environ.get("ADMIN_ROLE_ID")
WEBHOOK_URL_LOGS = os.environ.get("WEBHOOK_URL_LOGS")
TOP_ROLE_ID = os.environ.get("TOP_ROLE_ID")
GUILD_ID_STR = os.environ.get("GUILD_ID")

if not DISCORD_TOKEN:
    raise ValueError("DISCORD_TOKEN no está configurado")
if not ADMIN_ROLE_ID:
    raise ValueError("ADMIN_ROLE_ID no está configurado")
if not WEBHOOK_URL_LOGS:
    raise ValueError("WEBHOOK_URL_LOGS no está configurado")
if not TOP_ROLE_ID:
    raise ValueError("TOP_ROLE_ID no está configurado")
if not GUILD_ID_STR:
    raise ValueError("GUILD_ID no está configurado")

config = {
    "token": DISCORD_TOKEN,
    "admin_roles": [int(ADMIN_ROLE_ID)],
    "webhook_url_logs": WEBHOOK_URL_LOGS,
    "top_role_id": int(TOP_ROLE_ID)
}

GUILD_ID = int(GUILD_ID_STR)
work_cooldowns = {}

# -------------------------------
# Invitaciones (recompensa)
# -------------------------------
invite_uses = {}

# -------------------------------
# Historial de ganancias
# -------------------------------
HISTORY_FILE = "history.json"

def save_history_entry(user_id: int, motivo: str, cantidad: float):
    if not os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "w") as f:
            json.dump({}, f)
    with open(HISTORY_FILE, "r") as f:
        data = json.load(f)
    user_id = str(user_id)
    if user_id not in data:
        data[user_id] = []
    data[user_id].append({
        "motivo": motivo,
        "cantidad": cantidad,
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })
    with open(HISTORY_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get_history(user_id: int):
    if not os.path.exists(HISTORY_FILE):
        return []
    with open(HISTORY_FILE, "r") as f:
        data = json.load(f)
    return data.get(str(user_id), [])

# -------------------------------
# Bot principal
# -------------------------------
class YangaBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(command_prefix="/", intents=intents)

    async def setup_hook(self):
        guild = self.get_guild(GUILD_ID) or discord.Object(id=GUILD_ID)

        # -------------------------------
        # Préstamos
        # -------------------------------
        prestamos = {}  # { (deudor_id, acreedor_id): {monto, interes, timestamp, aceptado} }

        class PrestamoView(discord.ui.View):
            def __init__(self, deudor, acreedor, monto, interes, tipo):
                super().__init__(timeout=60)
                self.deudor = deudor
                self.acreedor = acreedor
                self.monto = monto
                self.interes = interes
                self.tipo = tipo
                self.aceptado = False

            @discord.ui.button(label="Aceptar", style=discord.ButtonStyle.green)
            async def aceptar(self, interaction: discord.Interaction, button: discord.ui.Button):
                if (self.tipo == "pedir" and interaction.user.id != self.acreedor.id) or \
                   (self.tipo == "ofrecer" and interaction.user.id != self.deudor.id):
                    await interaction.response.send_message("No eres quien debe aceptar esta oferta.", ephemeral=True)
                    return
                prestamos[(self.deudor.id, self.acreedor.id)] = {
                    "monto": self.monto,
                    "interes": self.interes,
                    "timestamp": int(time.time()),
                    "aceptado": True
                }
                self.aceptado = True
                self.stop()
                await interaction.response.send_message("✅ Préstamo aceptado.", ephemeral=False)
                await send_log(f"Préstamo aceptado: {self.deudor.mention} ↔ {self.acreedor.mention} | {self.monto} Y$ al {self.interes}% diario.")

            @discord.ui.button(label="Rechazar", style=discord.ButtonStyle.red)
            async def rechazar(self, interaction: discord.Interaction, button: discord.ui.Button):
                if (self.tipo == "pedir" and interaction.user.id != self.acreedor.id) or \
                   (self.tipo == "ofrecer" and interaction.user.id != self.deudor.id):
                    await interaction.response.send_message("No eres quien debe rechazar esta oferta.", ephemeral=True)
                    return
                self.stop()
                await interaction.response.send_message("❌ Préstamo rechazado.", ephemeral=False)

        @self.tree.command(name="prestamo_pedir", description="Pide un préstamo a un usuario", guild=guild)
        @app_commands.describe(usuario="A quién le pides el préstamo", monto="Cantidad de Y$", interes="Interés diario (%)")
        async def prestamo_pedir(interaction: discord.Interaction, usuario: discord.Member, monto: int, interes: float):
            if usuario.id == interaction.user.id:
                await interaction.response.send_message("No puedes pedirte un préstamo a ti mismo.", ephemeral=True)
                return
            if monto <= 0 or interes <= 0:
                await interaction.response.send_message("Monto e interés deben ser mayores a 0.", ephemeral=True)
                return
            view = PrestamoView(interaction.user, usuario, monto, interes, "pedir")
            embed = discord.Embed(
                title="Solicitud de Préstamo",
                description=f"{interaction.user.mention} solicita **{monto} Y$** a {usuario.mention} al **{interes}%** de interés diario.\n\n¿Aceptas?",
                color=0x3498db
            )
            await interaction.response.send_message(content=usuario.mention, embed=embed, view=view)
            await send_log(f"{interaction.user.mention} pidió préstamo de {monto} Y$ a {usuario.mention} ({interes}% diario)")

        @self.tree.command(name="prestamo_ofrecer", description="Ofrece un préstamo a un usuario", guild=guild)
        @app_commands.describe(usuario="A quién le ofreces el préstamo", monto="Cantidad de Y$", interes="Interés diario (%)")
        async def prestamo_ofrecer(interaction: discord.Interaction, usuario: discord.Member, monto: int, interes: float):
            if usuario.id == interaction.user.id:
                await interaction.response.send_message("No puedes ofrecerte un préstamo a ti mismo.", ephemeral=True)
                return
            if monto <= 0 or interes <= 0:
                await interaction.response.send_message("Monto e interés deben ser mayores a 0.", ephemeral=True)
                return
            view = PrestamoView(usuario, interaction.user, monto, interes, "ofrecer")
            embed = discord.Embed(
                title="Oferta de Préstamo",
                description=f"{interaction.user.mention} ofrece **{monto} Y$** a {usuario.mention} al **{interes}%** de interés diario.\n\n¿Aceptas?",
                color=0x2ecc71
            )
            await interaction.response.send_message(content=usuario.mention, embed=embed, view=view)
            await send_log(f"{interaction.user.mention} ofreció préstamo de {monto} Y$ a {usuario.mention} ({interes}% diario)")

        @self.tree.command(name="prestamo_deuda", description="Ver tu deuda con un usuario", guild=guild)
        @app_commands.describe(usuario="Usuario acreedor")
        async def prestamo_deuda(interaction: discord.Interaction, usuario: discord.Member):
            key = (interaction.user.id, usuario.id)
            if key not in prestamos or not prestamos[key]["aceptado"]:
                await interaction.response.send_message("No tienes deuda activa con ese usuario.", ephemeral=True)
                return
            prestamo = prestamos[key]
            dias = int((time.time() - prestamo["timestamp"]) // 86400)
            deuda = prestamo["monto"] * ((1 + prestamo["interes"]/100) ** dias)
            deuda = round(deuda, 2)
            await interaction.response.send_message(
                f"💸 Debes a {usuario.mention}: **{deuda} Y$**\n"
                f"Préstamo original: {prestamo['monto']} Y$\n"
                f"Interés diario: {prestamo['interes']}%\n"
                f"Días transcurridos: {dias}"
            )

        @self.tree.command(name="prestamo_pagar", description="Paga tu deuda a un usuario", guild=guild)
        @app_commands.describe(usuario="Usuario acreedor")
        async def prestamo_pagar(interaction: discord.Interaction, usuario: discord.Member):
            key = (interaction.user.id, usuario.id)
            if key not in prestamos or not prestamos[key]["aceptado"]:
                await interaction.response.send_message("No tienes deuda activa con ese usuario.", ephemeral=True)
                return
            prestamo = prestamos[key]
            dias = int((time.time() - prestamo["timestamp"]) // 86400)
            deuda = prestamo["monto"] * ((1 + prestamo["interes"]/100) ** dias)
            deuda = round(deuda, 2)
            bal = await economy.get_balance_user(interaction.user.id)
            if bal < deuda:
                await interaction.response.send_message(f"No tienes suficiente Y$ para pagar la deuda (**{deuda} Y$**).", ephemeral=True)
                return
            await economy.remove_money(interaction.user.id, deuda)
            await economy.give_money(usuario.id, deuda)
            del prestamos[key]
            await interaction.response.send_message(f"✅ Has pagado tu deuda de **{deuda} Y$** a {usuario.mention}.")
            await send_log(f"{interaction.user.mention} pagó su deuda de {deuda} Y$ a {usuario.mention}.")

        # -------------------------------
        # /history
        # -------------------------------
        @self.tree.command(name="history", description="Ver historial de cómo un usuario consiguió su dinero", guild=guild)
        @app_commands.describe(usuario="Usuario a consultar")
        async def history(interaction: discord.Interaction, usuario: discord.Member):
            historial = get_history(usuario.id)
            if not historial:
                await interaction.response.send_message(f"No hay historial para {usuario.display_name}.", ephemeral=True)
                return
            msg = f"**Historial de {usuario.display_name}:**\n"
            for entry in historial[-10:]:  # Muestra los últimos 10 movimientos
                msg += f"- {entry['fecha']}: {entry['motivo']} (+{entry['cantidad']} Y$)\n"
            await interaction.response.send_message(msg)

        # -------------------------------
        # /cf (coinflip)
        # -------------------------------
        class CoinflipView(discord.ui.View):
            def __init__(self, creator, amount):
                super().__init__(timeout=60)
                self.creator = creator
                self.amount = amount
                self.accepted = False
                self.acceptor = None

            @discord.ui.button(label="Aceptar Coinflip", style=discord.ButtonStyle.green)
            async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id == self.creator.id:
                    await interaction.response.send_message("No puedes aceptar tu propio coinflip.", ephemeral=True)
                    return
                bal = await economy.get_balance_user(interaction.user.id)
                if bal < self.amount:
                    await interaction.response.send_message("No tienes suficiente Y$ para aceptar.", ephemeral=True)
                    return
                self.accepted = True
                self.acceptor = interaction.user
                self.stop()
                await interaction.response.send_message(f"{interaction.user.mention} aceptó el coinflip. ¡Lanzando moneda!", ephemeral=False)

        @self.tree.command(name="cf", description="Apuesta contra otro usuario en un coinflip", guild=guild)
        @app_commands.describe(amount="Cantidad de Y$ a apostar")
        async def cf(interaction: discord.Interaction, amount: int):
            if amount <= 0:
                await interaction.response.send_message("La cantidad debe ser mayor a 0.", ephemeral=True)
                return
            bal = await economy.get_balance_user(interaction.user.id)
            if bal < amount:
                await interaction.response.send_message("No tienes suficiente Y$ para apostar.", ephemeral=True)
                return

            view = CoinflipView(interaction.user, amount)
            embed = discord.Embed(
                title="🪙 Coinflip",
                description=f"{interaction.user.mention} ha creado un coinflip de **{amount} Y$**.\n¡Haz clic en el botón para aceptar!",
                color=0xFFD700
            )
            msg = await interaction.response.send_message(embed=embed, view=view)
            await send_log(f"{interaction.user.mention} creó un coinflip de {amount} Y$.")

            await view.wait()

            if not view.accepted:
                await interaction.edit_original_response(content="⏳ Nadie aceptó el coinflip a tiempo.", embed=None, view=None)
                await send_log(f"Coinflip de {interaction.user.mention} por {amount} Y$ expiró sin ser aceptado.")
                return

            # Verifica balances de nuevo antes de resolver
            creator_bal = await economy.get_balance_user(interaction.user.id)
            acceptor_bal = await economy.get_balance_user(view.acceptor.id)
            if creator_bal < amount or acceptor_bal < amount:
                await interaction.edit_original_response(content="Uno de los usuarios ya no tiene suficiente Y$. Coinflip cancelado.", embed=None, view=None)
                await send_log(f"Coinflip cancelado por fondos insuficientes.")
                return

            # Coinflip
            winner = random.choice([interaction.user, view.acceptor])
            loser = view.acceptor if winner == interaction.user else interaction.user

            await economy.remove_money(interaction.user.id, amount)
            await economy.remove_money(view.acceptor.id, amount)
            await economy.give_money(winner.id, amount * 2)
            save_history_entry(winner.id, f"Ganó coinflip contra {loser.display_name}", amount * 2)

            result_embed = discord.Embed(
                title="🪙 Coinflip Resultado",
                description=f"¡{winner.mention} ganó el coinflip y recibe **{amount*2} Y$**!\n\nPerdedor: {loser.mention}",
                color=0x00FF00
            )
            await interaction.edit_original_response(embed=result_embed, view=None, content=None)
            await send_log(f"Coinflip: {interaction.user.mention} vs {view.acceptor.mention} por {amount} Y$. Ganador: {winner.mention}")

        # -------------------------------
        # /work
        # -------------------------------
        @self.tree.command(name="work", description="Haz un trabajo y gana Y$", guild=guild)
        async def work(interaction: discord.Interaction, job: Literal["pescador", "talador", "minero"]):
            user_id = interaction.user.id
            now = time.time()
            cooldown = 3600  # 1 hora

            if user_id in work_cooldowns and now - work_cooldowns[user_id] < cooldown:
                remaining = int(cooldown - (now - work_cooldowns[user_id]))
                minutes = remaining // 60
                seconds = remaining % 60
                await interaction.response.send_message(
                    f"⏳ Estás en cooldown. Intenta de nuevo en {minutes}m {seconds}s.", ephemeral=True
                )
                return

            result = await do_job(user_id, job)
            await interaction.response.send_message(f"{interaction.user.mention} {result}")

            # Enviar log al webhook solo si gana dinero
            if "No ganaste" not in result:
                async with aiohttp.ClientSession() as session:
                    webhook = discord.Webhook.from_url(config["webhook_url_logs"], session=session)
                    await webhook.send(f"{interaction.user.mention} ganó dinero trabajando: {result}")
                # Extrae la cantidad ganada del mensaje
                try:
                    cantidad_ganada = int([s for s in result.split() if s.isdigit()][0])
                except:
                    cantidad_ganada = 0
                save_history_entry(interaction.user.id, f"Trabajo: {job}", cantidad_ganada)

            work_cooldowns[user_id] = now

        # -------------------------------
        # /yanga add / remove
        # -------------------------------
        yango_group = app_commands.Group(name="yanga", description="Administración de Y$")

        @yango_group.command(name="add", description="Agregar Y$ a usuario, rol o todos")
        @app_commands.describe(member="Usuario a quien dar Y$", role="Rol a quien dar Y$", amount="Cantidad de Y$", message="Mensaje opcional")
        async def add(interaction: discord.Interaction, amount: int, member: discord.Member = None, role: discord.Role = None, message: str = None):
            if not any(r.id in config["admin_roles"] for r in interaction.user.roles):
                await interaction.response.send_message("❌ No tienes permisos.", ephemeral=True)
                return

            await interaction.response.defer()
            guild = interaction.guild
            text_log = ""

            if member:
                await economy.give_money(member.id, amount)
                save_history_entry(member.id, f"Admin: {interaction.user.display_name} dio Y$", amount)
                text_log = f"{interaction.user.mention} ha dado {amount} Y$ a {member.mention}."
            elif role:
                count = 0
                for m in guild.members:
                    if role in m.roles and not m.bot:
                        await economy.give_money(m.id, amount)
                        save_history_entry(m.id, f"Admin: {interaction.user.display_name} dio Y$ (rol)", amount)
                        count += 1
                text_log = f"{interaction.user.mention} ha dado {amount} Y$ a {count} miembros del rol {role.name}."
            else:
                count = 0
                for m in guild.members:
                    if not m.bot:
                        await economy.give_money(m.id, amount)
                        save_history_entry(m.id, f"Admin: {interaction.user.display_name} dio Y$ (todos)", amount)
                        count += 1
                text_log = f"{interaction.user.mention} ha dado {amount} Y$ a todos los miembros ({count})."

            if message:
                text_log += f" Mensaje: {message}"

            await send_log(text_log)
            await interaction.followup.send("✅ Operación realizada.")

        @yango_group.command(name="remove", description="Quitar Y$ a usuario, rol o todos")
        @app_commands.describe(member="Usuario a quien quitar Y$", role="Rol a quien quitar Y$", amount="Cantidad de Y$", message="Mensaje opcional")
        async def remove(interaction: discord.Interaction, amount: int, member: discord.Member = None, role: discord.Role = None, message: str = None):
            if not any(r.id in config["admin_roles"] for r in interaction.user.roles):
                await interaction.response.send_message("❌ No tienes permisos.", ephemeral=True)
                return

            await interaction.response.defer()
            guild = interaction.guild
            text_log = ""

            if member:
                await economy.remove_money(member.id, amount)
                text_log = f"{interaction.user.mention} ha quitado {amount} Y$ a {member.mention}."
            elif role:
                count = 0
                for m in guild.members:
                    if role in m.roles and not m.bot:
                        await economy.remove_money(m.id, amount)
                        count += 1
                text_log = f"{interaction.user.mention} ha quitado {amount} Y$ a {count} miembros del rol {role.name}."
            else:
                count = 0
                for m in guild.members:
                    if not m.bot:
                        await economy.remove_money(m.id, amount)
                        count += 1
                text_log = f"{interaction.user.mention} ha quitado {amount} Y$ a todos los miembros ({count})."

            if message:
                text_log += f" Mensaje: {message}"

            await send_log(text_log)
            await interaction.followup.send("✅ Operación realizada.")

        self.tree.add_command(yango_group, guild=guild)

        # -------------------------------
        # /balance
        # -------------------------------
        @self.tree.command(name="balance", description="Ver balance propio o de otro usuario", guild=guild)
        @app_commands.describe(member="Usuario opcional")
        async def balance(interaction: discord.Interaction, member: discord.Member = None):
            target = member or interaction.user
            bal = await economy.get_balance_user(target.id)
            await interaction.response.send_message(f"💰 {target.display_name} tiene **{bal} Y$**.")

        # -------------------------------
        # /balancetop
        # -------------------------------
        @self.tree.command(name="balancetop", description="Ver top de usuarios con más Y$", guild=guild)
        async def balancetop(interaction: discord.Interaction):
            top = await economy.get_top_users(10)
            msg = "🏆 **Top 10 de Y$**\n"
            rank = 1
            total_money = 0

            # Mostrar top 10
            for user_id, amount in top:
                member = interaction.guild.get_member(user_id)
                if member:
                    msg += f"{rank}. {member.display_name} → {amount} Y$\n"
                    rank += 1

            # Calcular dinero total en circulación usando todos los usuarios con Y$
            all_users_top = await economy.get_top_users(1000000)  # límite muy alto para incluir todos
            total_money = sum(amount for _, amount in all_users_top)

            msg += f"\n💰 **Total de Y$ en circulación:** {total_money} Y$"

            if rank == 1:
                msg += "\nNadie tiene Y$ aún."

            await interaction.response.send_message(msg)

        # -------------------------------
        # /transfer
        # -------------------------------
        @self.tree.command(name="transfer", description="Transferir Y$ a otro usuario", guild=guild)
        @app_commands.describe(member="Usuario a quien transferir", amount="Cantidad a transferir", message="Mensaje opcional")
        async def transfer(interaction: discord.Interaction, member: discord.Member, amount: int, message: str = None):
            sender = interaction.user
            bal = await economy.get_balance_user(sender.id)
            if bal < amount:
                await interaction.response.send_message("❌ No tienes suficiente Y$", ephemeral=True)
                return

            await economy.transfer_money(sender.id, member.id, amount)
            text_log = f"{sender.mention} ha transferido {amount} Y$ a {member.mention}."
            if message:
                text_log += f" Mensaje: {message}"

            await send_log(text_log)
            await interaction.response.send_message("✅ Transferencia realizada.")

        # -------------------------------
        # /fixroles
        # -------------------------------
        @self.tree.command(name="fixroles", description="Forzar reasignación del rol al top 1", guild=guild)
        async def fixroles(interaction: discord.Interaction):
            if not any(r.id in config["admin_roles"] for r in interaction.user.roles):
                await interaction.response.send_message("❌ No tienes permisos.", ephemeral=True)
                return
            guild = interaction.guild
            top_member = await update_top_role(guild)
            if top_member:
                await interaction.response.send_message(f"🔄 Rol actualizado. Ahora se le da el rol a **{top_member.display_name}**.")
            else:
                await interaction.response.send_message("❌ No se pudo asignar el rol (¿quizás no hay usuarios con Y$?).")

        await self.tree.sync(guild=guild)
        print("✅ Slash commands sincronizados en el servidor")

# -------------------------------
# Funciones auxiliares
# -------------------------------
async def send_log(message: str):
    async with aiohttp.ClientSession() as session:
        webhook = discord.Webhook.from_url(config["webhook_url_logs"], session=session)
        await webhook.send(message)

async def update_top_role(guild: discord.Guild):
    top = await economy.get_top_users(1)
    if not top:
        return None
    top_user_id, _ = top[0]
    try:
        top_member = guild.get_member(top_user_id) or await guild.fetch_member(top_user_id)
    except discord.NotFound:
        return None
    top_role = guild.get_role(config["top_role_id"])
    if not top_role:
        return None
    async for m in guild.fetch_members(limit=None):
        if top_role in m.roles and m.id != top_user_id:
            try:
                await m.remove_roles(top_role)
            except:
                pass
    if top_role not in top_member.roles:
        try:
            await top_member.add_roles(top_role)
        except:
            pass
    return top_member

# -------------------------------
# Bot
# -------------------------------
bot = YangaBot()

from database import init_db

@bot.event
async def on_ready():
    await init_db()
    print(f"✅ Bot conectado como {bot.user}")
    # Probar webhook solo al iniciar
    async with aiohttp.ClientSession() as session:
        webhook = discord.Webhook.from_url(config["webhook_url_logs"], session=session)
        await webhook.send("✅ Cajero activo y funcionando")
        print("✅ Webhook activa y funcionando")
    # Guardar usos de invitaciones actuales
    global invite_uses
    guild = bot.get_guild(GUILD_ID)
    invite_uses = {invite.code: invite.uses for invite in await guild.invites()}

@bot.event
async def on_member_join(member):
    global invite_uses
    guild = member.guild
    invites_before = invite_uses
    invites_after = {invite.code: invite.uses for invite in await guild.invites()}

    # Encuentra la invitación usada
    used_invite = None
    for code, uses in invites_after.items():
        if code in invites_before and uses > invites_before[code]:
            used_invite = code
            break

    invite_uses = invites_after

    if used_invite:
        invite = discord.utils.get(await guild.invites(), code=used_invite)
        if invite and invite.inviter and not member.bot:
            await economy.give_money(invite.inviter.id, 40)
            save_history_entry(invite.inviter.id, f"Invitó a {member}", 40)
            # Contar total de invitaciones de ese usuario
            total = sum(i.uses for i in await guild.invites() if i.inviter and i.inviter.id == invite.inviter.id)
            await send_log(f"{invite.inviter.mention} ha invitado a {member.mention} ({total} invitaciones)")

bot.run(config["token"])