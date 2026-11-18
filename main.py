import os

import asyncpg
import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

intents = discord.Intents.default()
intents.message_content = True


class MyClient(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.db = None

    async def setup_hook(self):
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL 환경 변수를 설정해 주세요.")

        # Railway에서 제공하는 postgres:// 주소도 연결할 수 있도록 변환
        database_url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        self.db = await asyncpg.create_pool(database_url)

        async with self.db.acquire() as connection:
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS registrations (
                    discord_id BIGINT PRIMARY KEY,
                    discord_name TEXT NOT NULL,
                    roblox_name TEXT NOT NULL,
                    roblox_key TEXT NOT NULL UNIQUE
                )
            """)

        await self.tree.sync()


client = MyClient()


@client.tree.command(name="등록", description="디스코드와 로블록스 정보를 등록합니다")
@app_commands.rename(
    discord_account="디스코드",
    roblox_nickname="로블록스",
)
@app_commands.describe(
    discord_account="등록할 디스코드 계정을 멘션으로 선택하세요",
    roblox_nickname="로블록스 닉네임을 입력하세요",
)
async def register(
    interaction: discord.Interaction,
    discord_account: discord.Member,
    roblox_nickname: str,
):
    roblox_name = roblox_nickname.strip()
    roblox_key = roblox_name.casefold()

    if not roblox_name:
        await interaction.response.send_message(
            "로블록스 닉네임을 입력해 주세요.",
            ephemeral=True,
        )
        return

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

            for index, row in enumerate(existing, start=1):
                embed.add_field(
                    name=f"등록 정보 {index}",
                    value=(
                        f"디스코드: <@{row['discord_id']}> "
                        f"({row['discord_name']})\n"
                        f"로블록스: {row['roblox_name']}"
                    ),
                    inline=False,
                )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True,
            )
            return

        try:
            await connection.execute(
                """
                INSERT INTO registrations
                    (discord_id, discord_name, roblox_name, roblox_key)
                VALUES ($1, $2, $3, $4)
                """,
                discord_account.id,
                str(discord_account),
                roblox_name,
                roblox_key,
            )
        except asyncpg.UniqueViolationError:
            # 거의 동시에 같은 계정이 등록된 경우에도 중복 등록을 막습니다.
            await interaction.response.send_message(
                "이미 등록된 계정입니다. 다시 확인해 주세요.",
                ephemeral=True,
            )
            return

    embed = discord.Embed(
        title="등록 처리 완료",
        color=discord.Color.green(),
    )
    embed.add_field(name="디스코드", value=discord_account.mention, inline=False)
    embed.add_field(name="로블록스", value=roblox_name, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


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
    raise RuntimeError(".env 또는 Railway Variables에 DISCORD_TOKEN을 설정해 주세요.")

client.run(TOKEN)
