import discord
import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"로그인 완료: {client.user}")

@client.event
async def on_message(message):
    # 봇 자신의 메시지에는 반응하지 않음
    if message.author.bot:
        return

    if message.content.strip() == "안녕":
        await message.reply("안녕하세요")

client.run(TOKEN)