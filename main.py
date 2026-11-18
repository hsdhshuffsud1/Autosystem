import os

import aiohttp
import asyncpg
import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# 이제 채팅 메시지 내용을 읽지 않으므로 Message Content Intent는 필요 없습니다.
intents = discord.Intents.default()

RANK_CHOICES = [
    app_commands.Choice(name="인턴", value="인턴"),
    app_commands.Choice(name="사원", value="사원"),
    app_commands.Choice(name="주임", value="주임"),
    app_commands.Choice(name="대리", value="대리"),
    app_commands.Choice(name="과장", value="과장"),
    app_commands.Choice(name="차장", value="차장"),
    app_commands.Choice(name="부장", value="부장"),
    app_commands.Choice(name="이사", value="이사"),
    app_commands.Choice(name="부대표", value="부대표"),
    app_commands.Choice(name="대표", value="대표"),
]


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


def make_duplicate_embed(rows):
    embed = discord.Embed(
        title="이미 등록된 계정입니다.",
        color=discord.Color.orange(),
    )

    for row in rows:
        rank_name = row["rank_name"] or "미등록"
        embed.add_field(
            name="등록 정보",
            value=(
                f"디스코드: <@{row['discord_id']}> "
                f"({row['discord_name']})\n"
                f"로블록스: {row['roblox_name']}\n"
                f"직급: {rank_name}"
            ),
            inline=False,
        )

    return embed


async def update_member_rank_roles(
    guild: discord.Guild,
    member: discord.Member,
    rank_name: str,
    roblox_name: str,
):
    """DB에 등록된 직급 역할을 적용하고 디스코드 별명을 변경합니다."""
    async with client.db.acquire() as connection:
        role_rows = await connection.fetch(
            """
            SELECT rank_name, role_id
            FROM rank_roles
            WHERE guild_id = $1
            """,
            guild.id,
        )

    role_ids_by_rank = {
        row["rank_name"]: row["role_id"]
        for row in role_rows
    }
    all_rank_role_ids = set(role_ids_by_rank.values())
    new_role_id = role_ids_by_rank.get(rank_name)

    role_message = ""
    old_roles = [
        role
        for role in member.roles
        if role.id in all_rank_role_ids and role.id != new_role_id
    ]

    try:
        if old_roles:
            await member.remove_roles(
                *old_roles,
                reason=f"직급을 {rank_name}(으)로 변경",
            )

        if new_role_id is None:
            role_message = "이 직급에 등록된 역할이 없어 역할은 추가하지 않았어요."
        else:
            new_role = guild.get_role(new_role_id)

            if new_role is None:
                role_message = (
                    "등록된 직급 역할을 서버에서 찾을 수 없어 역할을 추가하지 못했어요."
                )
            elif new_role not in member.roles:
                await member.add_roles(
                    new_role,
                    reason=f"{rank_name} 직급 적용",
                )
                role_message = f"{new_role.mention} 역할을 적용했어요."
            else:
                role_message = f"{new_role.mention} 역할이 이미 적용되어 있어요."

    except discord.Forbidden:
        role_message = (
            "역할을 변경하지 못했어요. 봇에 역할 관리 권한을 주고 "
            "봇 역할을 대상 역할보다 위로 옮겨 주세요."
        )
    except discord.HTTPException:
        role_message = "Discord 역할 변경 중 오류가 발생했어요."

    nickname = f"{rank_name} {roblox_name}"

    try:
        await member.edit(
            nick=nickname,
            reason=f"{rank_name} 직급 정보 적용",
        )
        nickname_message = f"별명을 `{nickname}`으로 변경했어요."
    except discord.Forbidden:
        nickname_message = (
            "별명을 변경하지 못했어요. 봇에 별명 관리 권한이 있는지 확인해 주세요."
        )
    except discord.HTTPException:
        nickname_message = "Discord 별명 변경 중 오류가 발생했어요."

    return nickname_message, role_message


class MyClient(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.db = None

    async def setup_hook(self):
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL 환경 변수를 설정해 주세요.")

        database_url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        self.db = await asyncpg.create_pool(database_url)

        async with self.db.acquire() as connection:
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS registrations (
                    discord_id BIGINT PRIMARY KEY,
                    discord_name TEXT NOT NULL,
                    roblox_name TEXT NOT NULL,
                    roblox_key TEXT NOT NULL UNIQUE,
                    roblox_id BIGINT,
                    rank_name TEXT
                )
            """)

            # 기존 테이블을 사용하는 경우에도 새 컬럼을 추가합니다.
            await connection.execute("""
                ALTER TABLE registrations
                ADD COLUMN IF NOT EXISTS roblox_id BIGINT
            """)
            await connection.execute("""
                ALTER TABLE registrations
                ADD COLUMN IF NOT EXISTS rank_name TEXT
            """)

            await connection.execute("""
                CREATE TABLE IF NOT EXISTS rank_roles (
                    guild_id BIGINT NOT NULL,
                    rank_name TEXT NOT NULL,
                    role_id BIGINT NOT NULL,
                    PRIMARY KEY (guild_id, rank_name)
                )
            """)

        await self.tree.sync()


client = MyClient()


# /직급 명령어 그룹
rank_group = app_commands.Group(
    name="직급",
    description="직급과 역할을 관리합니다",
)


@rank_group.command(
    name="역할등록",
    description="직급에 연결할 서버 역할을 등록합니다",
)
@app_commands.rename(rank="직급", role="역할")
@app_commands.choices(rank=RANK_CHOICES)
async def register_rank_role(
    interaction: discord.Interaction,
    rank: app_commands.Choice[str],
    role: discord.Role,
):
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "이 명령어는 서버 안에서만 사용할 수 있어요.",
            ephemeral=True,
        )
        return

    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message(
            "역할을 등록하려면 역할 관리 권한이 필요해요.",
            ephemeral=True,
        )
        return

    async with client.db.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO rank_roles (guild_id, rank_name, role_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id, rank_name)
            DO UPDATE SET role_id = EXCLUDED.role_id
            """,
            interaction.guild_id,
            rank.value,
            role.id,
        )

    await interaction.response.send_message(
        f"직급 **{rank.value}**에 역할 {role.mention}을 등록했어요.",
        ephemeral=True,
    )


@rank_group.command(
    name="관리",
    description="등록된 사용자의 직급과 역할을 변경합니다",
)
@app_commands.rename(member="대상", rank="직급")
@app_commands.choices(rank=RANK_CHOICES)
async def manage_rank(
    interaction: discord.Interaction,
    member: discord.Member,
    rank: app_commands.Choice[str],
):
    if interaction.guild is None:
        await interaction.response.send_message(
            "이 명령어는 서버 안에서만 사용할 수 있어요.",
            ephemeral=True,
        )
        return

    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message(
            "직급을 관리하려면 역할 관리 권한이 필요해요.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    async with client.db.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT roblox_name
            FROM registrations
            WHERE discord_id = $1
            """,
            member.id,
        )

        if row is None:
            await interaction.followup.send(
                "이 디스코드 계정은 등록되어 있지 않아 직급을 변경할 수 없어요.",
                ephemeral=True,
            )
            return

        await connection.execute(
            """
            UPDATE registrations
            SET rank_name = $1
            WHERE discord_id = $2
            """,
            rank.value,
            member.id,
        )

    nickname_message, role_message = await update_member_rank_roles(
        guild=interaction.guild,
        member=member,
        rank_name=rank.value,
        roblox_name=row["roblox_name"],
    )

    embed = discord.Embed(
        title="직급 변경 완료",
        color=discord.Color.blue(),
    )
    embed.add_field(name="대상", value=member.mention, inline=False)
    embed.add_field(name="새 직급", value=rank.value, inline=False)
    embed.description = f"{nickname_message}\n{role_message}"

    await interaction.followup.send(embed=embed, ephemeral=True)


client.tree.add_command(rank_group)


# /등록 명령어
@client.tree.command(
    name="등록",
    description="디스코드, 로블록스 계정과 직급을 등록합니다",
)
@app_commands.rename(
    discord_account="디스코드",
    roblox_nickname="로블록스",
    rank="직급",
)
@app_commands.describe(
    discord_account="등록할 디스코드 계정을 선택하세요",
    roblox_nickname="Roblox 사용자 이름을 입력하세요",
    rank="등록할 직급을 선택하세요",
)
@app_commands.choices(rank=RANK_CHOICES)
async def register(
    interaction: discord.Interaction,
    discord_account: discord.Member,
    roblox_nickname: str,
    rank: app_commands.Choice[str],
):
    roblox_name = roblox_nickname.strip()

    if not roblox_name:
        await interaction.response.send_message(
            "로블록스 사용자 이름을 입력해 주세요.",
            ephemeral=True,
        )
        return

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

    roblox_name = roblox_user["name"]
    roblox_key = roblox_name.casefold()
    roblox_id = int(roblox_user["id"])
    rank_name = rank.value

    async with client.db.acquire() as connection:
        existing = await connection.fetch(
            """
            SELECT discord_id, discord_name, roblox_name, rank_name
            FROM registrations
            WHERE discord_id = $1 OR roblox_key = $2
            """,
            discord_account.id,
            roblox_key,
        )

        if existing:
            await interaction.followup.send(
                embed=make_duplicate_embed(existing),
                ephemeral=True,
            )
            return

        try:
            await connection.execute(
                """
                INSERT INTO registrations
                    (
                        discord_id,
                        discord_name,
                        roblox_name,
                        roblox_key,
                        roblox_id,
                        rank_name
                    )
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                discord_account.id,
                str(discord_account),
                roblox_name,
                roblox_key,
                roblox_id,
                rank_name,
            )
        except asyncpg.UniqueViolationError:
            existing = await connection.fetch(
                """
                SELECT discord_id, discord_name, roblox_name, rank_name
                FROM registrations
                WHERE discord_id = $1 OR roblox_key = $2
                """,
                discord_account.id,
                roblox_key,
            )
            await interaction.followup.send(
                embed=make_duplicate_embed(existing),
                ephemeral=True,
            )
            return

    nickname_message, role_message = await update_member_rank_roles(
        guild=interaction.guild,
        member=discord_account,
        rank_name=rank_name,
        roblox_name=roblox_name,
    )

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
    embed.add_field(
        name="직급",
        value=rank_name,
        inline=False,
    )
    embed.description = f"{nickname_message}\n{role_message}"

    await interaction.followup.send(embed=embed, ephemeral=True)


# /정보 명령어
@client.tree.command(
    name="정보",
    description="등록된 계정 정보를 확인합니다",
)
@app_commands.rename(member="디스코드")
@app_commands.describe(
    member="정보를 확인할 디스코드 계정을 선택하세요",
)
async def info(
    interaction: discord.Interaction,
    member: discord.Member,
):
    await interaction.response.defer(ephemeral=True)

    async with client.db.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT discord_id, roblox_name, roblox_id, rank_name
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

        # 이전에 등록된 데이터에 Roblox ID가 없으면 찾아서 보완합니다.
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

    embed = discord.Embed(
        title="등록 정보",
        color=discord.Color.blue(),
    )
    embed.add_field(name="디스코드", value=member.mention, inline=False)
    embed.add_field(
        name="직급",
        value=row["rank_name"] or "미등록",
        inline=False,
    )
    embed.add_field(
        name="로블록스",
        value=f"[{row['roblox_name']}]({profile_url})",
        inline=False,
    )

    await interaction.followup.send(embed=embed, ephemeral=True)


@client.event
async def on_ready():
    print(f"로그인 완료: {client.user}")


if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN을 설정해 주세요.")

client.run(TOKEN)
