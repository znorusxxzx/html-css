# bot.py
from __future__ import annotations
import os
import json
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, button

# -------------- CONFIG --------------
TOKEN = os.getenv("DISCORD_TOKEN")  # coloque no .env
GUILD_ID_FOR_SYNC = None  # opcional: ID do servidor para sync mais rápido, ou None
TRANSFER_CHANNEL_ID = 1425862319765848166  # canal onde mandar as transferências
PING_ID = 1426543374952956074  # cargo OLHEIROS
DATA_FILE = "transferencias.json"
# ------------------------------------

intents = discord.Intents.default()
intents.members = True
intents.message_content = False

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

pending_offers: dict[int, dict] = {}


# ----- helpers -----
def save_transfer(record: dict):
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = []
    except Exception:
        data = []

    data.append(record)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def find_trainer_role(member: discord.Member) -> discord.Role | None:
    """Retorna a role do tipo 'Treinador X' do membro."""
    for role in member.roles:
        if role.name.startswith("Treinador "):
            return role
    return None


def team_name_from_trainer_role_name(trainer_role_name: str) -> str:
    return trainer_role_name[len("Treinador "):].strip()


def fmt_transfer_message(team_name: str, direction: str, jogador_mention: str, guild: discord.Guild) -> str:
    """Formata a mensagem de transferência com contador de jogadores."""
    team_role = discord.utils.get(guild.roles, name=team_name)
    count = 0
    if team_role:
        count = sum(1 for m in guild.members if team_role in m.roles)
    max_players = 20

    return (
        f"## Transferência | {team_name}\n"
        f"### {direction}\n"
        f"### Jogador: {jogador_mention}\n"
        f"- Posição: desconhecida\n"
        f"- Valor de Mercado: Livre\n"
        f"- {datetime.utcnow().strftime('%d/%m')}\n"
        f"- {count}/{max_players}\n"
        f"### Ping: <@&{PING_ID}>"
    )


# ----- Views / Buttons -----
class OfferView(View):
    def __init__(self, target_id: int, offer_data: dict, timeout: int = 60 * 60 * 24):
        super().__init__(timeout=timeout)
        self.target_id = target_id
        self.offer_data = offer_data

    @button(label="Aceitar ✅", style=discord.ButtonStyle.success, custom_id="accept_offer")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id:
            await interaction.response.send_message("Somente o jogador convidado pode responder esse convite.", ephemeral=True)
            return

        guild: discord.Guild = bot.get_guild(self.offer_data["guild_id"])
        if guild is None:
            await interaction.response.send_message("Não consegui acessar o servidor.", ephemeral=True)
            return

        team_role = guild.get_role(self.offer_data["team_role_id"])
        trainer = guild.get_member(self.offer_data["trainer_id"])
        member = guild.get_member(interaction.user.id)

        if member is None:
            await interaction.response.send_message("Você não está mais no servidor.", ephemeral=True)
            return

        existing_team_role = None
        for r in member.roles:
            if r.is_default():
                continue
            trainer_role_name = f"Treinador {r.name}"
            if any(x.name == trainer_role_name for x in guild.roles):
                existing_team_role = r
                break

        if existing_team_role:
            await interaction.response.send_message(
                f"Você já tem o cargo de time `{existing_team_role.name}` — remova-o antes de aceitar outro contrato.",
                ephemeral=True
            )
            return

        if team_role is None:
            await interaction.response.send_message("Cargo do time não encontrado.", ephemeral=True)
            return

        try:
            await member.add_roles(team_role, reason=f"Contratado por {trainer.display_name if trainer else self.offer_data['trainer_id']}")
        except Exception as e:
            await interaction.response.send_message(f"Falha ao dar o cargo: {e}", ephemeral=True)
            return

        chan = guild.get_channel(TRANSFER_CHANNEL_ID)
        mention = member.mention
        msg = fmt_transfer_message(team_role.name, f"Livre →→→ {team_role.name}", mention, guild)
        if chan:
            await chan.send(msg)

        record = {
            "jogador_id": member.id,
            "jogador_name": str(member),
            "time": team_role.name,
            "action": "contratado",
            "trainer_id": trainer.id if trainer else None,
            "timestamp": datetime.utcnow().isoformat()
        }
        save_transfer(record)
        pending_offers.pop(member.id, None)

        await interaction.response.edit_message(content=f"✅ Você aceitou o contrato com **{team_role.name}**. Boa sorte!", view=None)

        try:
            if trainer:
                await trainer.send(f"✅ {member.display_name} aceitou o contrato com `{team_role.name}`.")
        except Exception:
            pass

    @button(label="Recusar ❌", style=discord.ButtonStyle.danger, custom_id="decline_offer")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id:
            await interaction.response.send_message("Somente o jogador convidado pode responder esse convite.", ephemeral=True)
            return

        guild: discord.Guild = bot.get_guild(self.offer_data["guild_id"])
        trainer = guild.get_member(self.offer_data["trainer_id"]) if guild else None
        member = guild.get_member(interaction.user.id) if guild else None

        pending_offers.pop(interaction.user.id, None)
        await interaction.response.edit_message(content="❌ Você recusou o contrato.", view=None)

        if trainer:
            try:
                await trainer.send(f"❌ {member.display_name if member else interaction.user.name} recusou o contrato.")
            except Exception:
                pass


# ----- Slash commands -----
@tree.command(name="contratar", description="Convida um jogador para o seu time", guild=discord.Object(id=GUILD_ID_FOR_SYNC) if GUILD_ID_FOR_SYNC else None)
@app_commands.describe(user="Jogador a ser contratado (menção)")
async def contratar(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)
    invoker: discord.Member = interaction.user
    guild = interaction.guild
    if guild is None:
        await interaction.followup.send("Esse comando só pode ser usado dentro do servidor.", ephemeral=True)
        return

    trainer_role = find_trainer_role(invoker)
    if not trainer_role:
        await interaction.followup.send("Você precisa ter um cargo `Treinador <Time>` para contratar jogadores.", ephemeral=True)
        return

    team_name = team_name_from_trainer_role_name(trainer_role.name)
    team_role = discord.utils.get(guild.roles, name=team_name)
    if team_role is None:
        await interaction.followup.send(f"Cargo de time `{team_name}` não encontrado.", ephemeral=True)
        return

    if user.bot:
        await interaction.followup.send("Não é possível contratar bots.", ephemeral=True)
        return

    for r in user.roles:
        if r.is_default():
            continue
        matching_trainer_role_name = f"Treinador {r.name}"
        if any(x.name == matching_trainer_role_name for x in guild.roles):
            await interaction.followup.send(f"O jogador já pertence ao time `{r.name}`. Peça pra ele sair primeiro.", ephemeral=True)
            return

    offer = {
        "guild_id": guild.id,
        "trainer_id": invoker.id,
        "team_role_id": team_role.id,
        "timestamp": datetime.utcnow().isoformat()
    }
    pending_offers[user.id] = offer

    try:
        dm_channel = await user.create_dm()
        view = OfferView(target_id=user.id, offer_data=offer)
        await dm_channel.send(
            f"Você foi convidado para jogar pelo **{team_role.name}**.\nDeseja aceitar o contrato?",
            view=view
        )
    except discord.Forbidden:
        await interaction.followup.send("Não consegui enviar DM para o jogador.", ephemeral=True)
        pending_offers.pop(user.id, None)
        return

    await interaction.followup.send(f"✅ Convite enviado por DM para {user.mention}.", ephemeral=True)


@tree.command(name="demitir", description="Demitir um jogador do seu time", guild=discord.Object(id=GUILD_ID_FOR_SYNC) if GUILD_ID_FOR_SYNC else None)
@app_commands.describe(user="Jogador a ser demitido")
async def demitir(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)
    invoker: discord.Member = interaction.user
    guild = interaction.guild
    if guild is None:
        await interaction.followup.send("Use esse comando no servidor.", ephemeral=True)
        return

    trainer_role = find_trainer_role(invoker)
    if not trainer_role:
        await interaction.followup.send("Você precisa ser um `Treinador <Time>` para demitir.", ephemeral=True)
        return

    team_name = team_name_from_trainer_role_name(trainer_role.name)
    team_role = discord.utils.get(guild.roles, name=team_name)
    if team_role is None:
        await interaction.followup.send(f"Cargo de time `{team_name}` não encontrado.", ephemeral=True)
        return

    if team_role not in user.roles:
        await interaction.followup.send("O jogador não possui o cargo do seu time.", ephemeral=True)
        return

    try:
        await user.remove_roles(team_role, reason=f"Demitido por {invoker.display_name}")
    except Exception as e:
        await interaction.followup.send(f"Falha ao remover cargo: {e}", ephemeral=True)
        return

    chan = guild.get_channel(TRANSFER_CHANNEL_ID)
    msg = fmt_transfer_message(team_role.name, f"{team_role.name} →→→ Livre", user.mention, guild)
    if chan:
        await chan.send(msg)

    record = {
        "jogador_id": user.id,
        "jogador_name": str(user),
        "time": team_role.name,
        "action": "demitido",
        "trainer_id": invoker.id,
        "timestamp": datetime.utcnow().isoformat()
    }
    save_transfer(record)

    try:
        await user.send(f"Você foi demitido do time `{team_role.name}`.")
    except Exception:
        pass

    await interaction.followup.send(f"✅ {user.display_name} demitido de `{team_role.name}`.", ephemeral=True)


@tree.command(name="sair", description="Sair do seu time (perder cargo)", guild=discord.Object(id=GUILD_ID_FOR_SYNC) if GUILD_ID_FOR_SYNC else None)
async def sair(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    member: discord.Member = interaction.user
    guild = interaction.guild
    if guild is None:
        await interaction.followup.send("Use esse comando no servidor.", ephemeral=True)
        return

    current_team_role = None
    for r in member.roles:
        if r.is_default():
            continue
        tr_name = f"Treinador {r.name}"
        if any(x.name == tr_name for x in guild.roles):
            current_team_role = r
            break

    if current_team_role is None:
        await interaction.followup.send("Você não possui cargo de time para sair.", ephemeral=True)
        return

    try:
        await member.remove_roles(current_team_role, reason="Saiu do time via /sair")
    except Exception as e:
        await interaction.followup.send(f"Falha ao remover cargo: {e}", ephemeral=True)
        return

    chan = guild.get_channel(TRANSFER_CHANNEL_ID)
    msg = fmt_transfer_message(current_team_role.name, f"{current_team_role.name} →→→ Livre", member.mention, guild)
    if chan:
        await chan.send(msg)

    record = {
        "jogador_id": member.id,
        "jogador_name": str(member),
        "time": current_team_role.name,
        "action": "saiu",
        "timestamp": datetime.utcnow().isoformat()
    }
    save_transfer(record)

    await interaction.followup.send(f"✅ Você saiu do `{current_team_role.name}` e perdeu o cargo.", ephemeral=True)
@bot.event
async def on_message(message: discord.Message):
    # Ignora mensagens do próprio bot
    if message.author.bot:
        return

    # Lista de domínios ou padrões proibidos
    blocked_patterns = [
        "discord.gg/",
        "http://",
        "https://",
        "www."
    ]

    # Verifica se há link na mensagem
    if any(pattern in message.content.lower() for pattern in blocked_patterns):
        try:
            await message.delete()
            await message.channel.send(
                f"🚫 {message.author.mention}, o envio de links não é permitido aqui!",
                delete_after=5
            )
        except discord.Forbidden:
            print(f"[WARN] Sem permissão para deletar mensagens em {message.channel}.")
        except Exception as e:
            print(f"[ERRO] Falha ao deletar mensagem: {e}")
        return  # evita processar comandos de mensagem com link

    # Permite o processamento normal dos comandos (slash ou prefixo)
    await bot.process_commands(message)

# ----- startup / sync -----
@bot.event
async def on_ready():
    print(f"Bot pronto. Logado como {bot.user} (id: {bot.user.id})")
    if GUILD_ID_FOR_SYNC:
        guild = discord.Object(id=GUILD_ID_FOR_SYNC)
        await tree.sync(guild=guild)
        print("Comandos sincronizados para guild:", GUILD_ID_FOR_SYNC)
    else:
        await tree.sync()
        print("Comandos sincronizados globalmente.")

bot.run("MTQyNjA3MTI5NDQyMzYwMTE3Mg.G3PjK8.1XBB1bwwC8jGA7NCCIgfndnI8fwhsxz7jopJ4A")
