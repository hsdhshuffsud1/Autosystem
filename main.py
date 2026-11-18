import os

import aiohttp
import asyncpg
import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

intents = discord.Intents.default()
intents.message_content = True


async def find_roblox_user(username: str):
    """Roblox 사용자 이름으로 계정을 조회합니다."""
    url = "https://users.roblox.com/v1/usernames/users"
    payload = {
        "usernames": [username],
        "excludeBannedUsers": False,
    }

    timeout = aiohttp.ClientTimeout(total=10)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload) as response:
            response.raise_for_status()
            result = await response.json()

    users = result.get("data", [])
    return users[0] if users else None


class MyClient(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.db = None

    async def setup_hook(self):
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL 환경 변수를 설정해 주세요.")

        # Railway의 postgres:// 주소를 asyncpg 연결 형식으로 변환합니다.
        database_url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        self.db = await asyncpg.create_pool(database_url)

        async with self.db.acquire() as connection:
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS registrations (
                    discord_id BIGINT PRIMARY KEY,
                    discord_name TEXT NOT NULL,
                    roblox_name TEXT NOT NULL,
                    roblox_key TEXT NOT NULL UNIQUE,
                    roblox_id BIGINT
                )
            """)

            # 이미 존재하는 테이블에도 컬럼을 추가합니다.
            await connection.execute("""
                ALTER TABLE registrations
                ADD COLUMN IF NOT EXISTS roblox_id BIGINT
            """)

        await self.tree.sync()


client = MyClient()


@client.tree.command(name="등록", description="디스코드와 로블록스 정보를 등록합니다")
@app_commands.rename(
    discord_account="디스코드",
    roblox_nickname="로블록스",
)
@app_commands.describe(
    discord_account="등록할 디스코드 계정을 선택하세요",
    roblox_nickname="Roblox 사용자 이름을 입력하세요",
)
async def register(
    interaction: discord.Interaction,
    discord_account: discord.Member,
    roblox_nickname: str,
):
    roblox_name = roblox_nickname.strip()

    if not roblox_name:
        await interaction.response.send_message(
            "로블록스 사용자 이름을 입력해 주세요.",
            ephemeral=True,
        )
        return

    # Roblox 계정 확인 중 Discord 응답이 시간 초과되지 않게 합니다.
    await interaction.response.defer(ephemeral=True)

    try:
        roblox_user = await find_roblox_user(roblox_name)
    except (aiohttp.ClientError, TimeoutError):
        await interaction.followup.send(
            "Roblox 계정 확인에 실패했어요. 잠시 후 다시 시도해 주세요.",
            ephemeral=True,
        )
        return

    if roblox_user is None:
        await interaction.followup.send(
            "존재하지 않는 Roblox 사용자 이름입니다. 다시 확인해 주세요.",
            ephemeral=True,
        )
        return

    # Roblox에서 확인된 정식 사용자 이름과 고유 ID를 저장합니다.
    roblox_name = roblox_user["name"]
    roblox_key = roblox_name.casefold()
    roblox_id = int(roblox_user["id"])

    async with client.db.acquire() as connection:
        existing = await connection.fetch(
            """
            SELECT discord_id, discord_name, roblox_name
            FROM registrations
            WHERE discord_id = $1 OR roblox_key = $2
            """,
            discord_account.id,
            roblox_key,
        )

        if existing:
            embed = discord.Embed(
                title="이미 등록된 계정입니다.",
                color=discord.Color.orange(),
            )

            for row in existing:
                embed.add_field(
                    name="등록 정보",
                    value=(
                        f"디스코드: <@{row['discord_id']}> "
                        f"({row['discord_name']})\n"
                        f"로블록스: {row['roblox_name']}"
                    ),
                    inline=False,
                )

            await interaction.followup.send(
                embed=embed,
                ephemeral=True,
            )
            return

        try:
            await connection.execute(
                """
                INSERT INTO registrations
                    (discord_id, discord_name, roblox_name, roblox_key, roblox_id)
                VALUES ($1, $2, $3, $4, $5)
                """,
                discord_account.id,
                str(discord_account),
                roblox_name,
                roblox_key,
                roblox_id,
            )
        except asyncpg.UniqueViolationError:
            # 거의 동시에 같은 계정이 등록된 경우에도 중복 저장을 막습니다.
            existing = await connection.fetch(
                """
                SELECT discord_id, discord_name, roblox_name
                FROM registrations
                WHERE discord_id = $1 OR roblox_key = $2
                """,
                discord_account.id,
                roblox_key,
            )

            embed = discord.Embed(
                title="이미 등록된 계정입니다.",
                color=discord.Color.orange(),
            )

            for row in existing:
                embed.add_field(
                    name="등록 정보",
                    value=(
                        f"디스코드: <@{row['discord_id']}> "
                        f"({row['discord_name']})\n"
                        f"로블록스: {row['roblox_name']}"
                    ),
                    inline=False,
                )

            await interaction.followup.send(
                embed=embed,
                ephemeral=True,
            )
            return

    embed = discord.Embed(
        title="등록 처리 완료",
        color=discord.Color.green(),
    )
    embed.add_field(
        name="디스코드",
        value=discord_account.mention,
        inline=False,
    )
    embed.add_field(
        name="로블록스",
        value=roblox_name,
        inline=False,
    )

    await interaction.followup.send(
        embed=embed,
        ephemeral=True,
    )


@client.tree.command(name="정보", description="등록된 Roblox 계정 정보를 확인합니다")
@app_commands.rename(member="디스코드")
@app_commands.describe(member="정보를 확인할 디스코드 계정을 선택하세요")
async def info(
    interaction: discord.Interaction,
    member: discord.Member,
):
    await interaction.response.defer(ephemeral=True)

    async with client.db.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT discord_id, roblox_name, roblox_id
            FROM registrations
            WHERE discord_id = $1
            """,
            member.id,
        )

        if row is None:
            await interaction.followup.send(
                "등록된 정보가 없습니다.",
                ephemeral=True,
            )
            return

        roblox_id = row["roblox_id"]

        # 예전에 등록해 roblox_id가 없는 데이터는 조회해서 보완합니다.
        if roblox_id is None:
            try:
                roblox_user = await find_roblox_user(row["roblox_name"])
            except (aiohttp.ClientError, TimeoutError):
                await interaction.followup.send(
                    "Roblox 계정 확인에 실패했어요. 잠시 후 다시 시도해 주세요.",
                    ephemeral=True,
                )
                return

            if roblox_user is None:
                await interaction.followup.send(
                    "저장된 Roblox 계정을 찾을 수 없습니다.",
                    ephemeral=True,
                )
                return

            roblox_id = int(roblox_user["id"])

            await connection.execute(
                """
                UPDATE registrations
                SET roblox_id = $1
                WHERE discord_id = $2
                """,
                roblox_id,
                member.id,
            )

    profile_url = f"https://www.roblox.com/users/{roblox_id}/profile"

    await interaction.followup.send(
        f"{member.mention}의 Roblox 계정: "
        f"[{row['roblox_name']}]({profile_url})",
        ephemeral=True,
    )


@client.event
async def on_ready():
    print(f"로그인 완료: {client.user}")


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if message.content.strip() == "안녕":
        await message.reply("안녕하세요")


if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN을 설정해 주세요.")

client.run(TOKEN)
