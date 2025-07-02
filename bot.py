import discord
from discord.ext import commands
from discord.ui import Button, View
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import aiohttp
from datetime import datetime, timedelta
import asyncio
import os

# Botの権限設定（message_contentはスラッシュコマンド以外でメッセージ内容取得に必要）
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # DM送信に必須

bot = commands.Bot(command_prefix='/', intents=intents)
scheduler = AsyncIOScheduler()
scheduler.start()

# 問い合わせ・バグ報告用WebhookURL
WEBHOOK_URL = "https://discordapp.com/api/webhooks/1389820485075996692/t1ni8tLiFS9JykVy7Km15M-GFDywk4HxZazySSKiMm2_dOuhKfCgWmuQxZiE23l-vFsi"

# スケジュールデータ保存用（本来はDB推奨）
schedules = {}

# Reportコマンドの送信時間管理（2時間に1回制限）
last_report_time = {}

# 参加可・不可のボタンViewクラス
class ParticipationView(View):
    def __init__(self, title):
        super().__init__(timeout=None)  # タイムアウトしない
        self.title = title

    @discord.ui.button(label="参加可能", style=discord.ButtonStyle.success, custom_id="participate_yes")
    async def participate_yes(self, interaction: discord.Interaction, button: Button):
        user_id = interaction.user.id
        schedule = schedules.get(self.title)
        if not schedule:
            await interaction.response.send_message("このスケジュールは既に削除されています。", ephemeral=True)
            return
        schedule["participants"].add(user_id)
        schedule["non_participants"].discard(user_id)
        await interaction.response.send_message(f"{interaction.user.name} さんを「参加可能」に登録しました。", ephemeral=True)

    @discord.ui.button(label="参加不可", style=discord.ButtonStyle.danger, custom_id="participate_no")
    async def participate_no(self, interaction: discord.Interaction, button: Button):
        user_id = interaction.user.id
        schedule = schedules.get(self.title)
        if not schedule:
            await interaction.response.send_message("このスケジュールは既に削除されています。", ephemeral=True)
            return
        schedule["non_participants"].add(user_id)
        schedule["participants"].discard(user_id)
        await interaction.response.send_message(f"{interaction.user.name} さんを「参加不可」に登録しました。", ephemeral=True)

# スケジュール時間になったら参加可能者にDMを送る非同期関数
async def send_schedule_dm(title):
    await bot.wait_until_ready()  # Botが完全に起動するまで待機
    schedule = schedules.get(title)
    if not schedule:
        return
    detail = schedule["detail"]
    dt = schedule["datetime"]
    participants = schedule["participants"]
    for user_id in participants:
        user = bot.get_user(user_id)
        if user:
            try:
                await user.send(
                    f"【スケジュール通知】\nタイトル: {title}\n日時: {dt.strftime('%Y-%m-%d %H:%M')}\n詳細: {detail}"
                )
            except Exception as e:
                print(f"DM送信失敗: {user} - {e}")

# APSchedulerは同期関数を使うため、非同期関数をスレッドセーフに呼ぶラッパー
def schedule_job(title, dt):
    def job_wrapper():
        asyncio.run_coroutine_threadsafe(send_schedule_dm(title), bot.loop)
    try:
        scheduler.add_job(job_wrapper, "date", run_date=dt, id=title)
    except Exception as e:
        print(f"ジョブ登録エラー: {e}")

# /Create タイトル 日時 詳細
@bot.command()
async def Create(ctx, title: str, datetime_str: str, *, detail: str):
    """
    例: /Create 会議 2025-07-02T15:30 重要会議です
    日時はISO8601形式 YYYY-MM-DDTHH:MM を想定
    """
    if title in schedules:
        await ctx.send(f"「{title}」は既に存在します。")
        return
    try:
        dt = datetime.fromisoformat(datetime_str)
    except Exception:
        await ctx.send("日時の形式が間違っています。例: 2025-07-02T15:30")
        return
    schedules[title] = {
        "datetime": dt,
        "detail": detail,
        "message_id": None,
        "channel_id": ctx.channel.id,
        "participants": set(),
        "non_participants": set()
    }
    embed = discord.Embed(title=f"スケジュール作成: {title}", description=detail)
    embed.add_field(name="日時", value=dt.strftime("%Y-%m-%d %H:%M"))
    embed.set_footer(text="参加可能かどうかボタンで選択してください")
    view = ParticipationView(title)
    message = await ctx.send(embed=embed, view=view)
    schedules[title]["message_id"] = message.id
    schedule_job(title, dt)
    await ctx.send(f"スケジュール「{title}」を作成しました。")

# /Edit タイトル [日時] [詳細]
@bot.command()
async def Edit(ctx, title: str, datetime_str: str = None, *, detail: str = None):
    """
    例: /Edit 会議 2025-07-02T16:00 新しい内容
    datetime_str、detailは任意
    """
    schedule = schedules.get(title)
    if not schedule:
        await ctx.send(f"「{title}」は見つかりません。")
        return
    if datetime_str:
        try:
            dt = datetime.fromisoformat(datetime_str)
            schedule["datetime"] = dt
            try:
                scheduler.remove_job(title)
            except Exception:
                pass
            schedule_job(title, dt)
        except Exception:
            await ctx.send("日時の形式が間違っています。例: 2025-07-02T15:30")
            return
    if detail:
        schedule["detail"] = detail
    channel = bot.get_channel(schedule["channel_id"])
    if not channel:
        await ctx.send("チャンネルが見つかりません。")
        return
    try:
        message = await channel.fetch_message(schedule["message_id"])
    except Exception:
        message = None
    embed = discord.Embed(title=f"スケジュール編集: {title}", description=schedule["detail"])
    embed.add_field(name="日時", value=schedule["datetime"].strftime("%Y-%m-%d %H:%M"))
    embed.set_footer(text="参加可能かどうかボタンで選択してください")
    view = ParticipationView(title)
    if message:
        await message.edit(embed=embed, view=view)
        await ctx.send(f"「{title}」のスケジュールを更新しました。")
    else:
        await ctx.send(f"メッセージが見つからず編集できませんでした。")

# /Delete タイトル
@bot.command()
async def Delete(ctx, title: str):
    schedule = schedules.get(title)
    if not schedule:
        await ctx.send(f"「{title}」は存在しません。")
        return
    schedules.pop(title)
    try:
        scheduler.remove_job(title)
    except Exception:
        pass
    await ctx.send(f"スケジュール「{title}」を削除しました。")

# /Report 内容
@bot.command()
async def Report(ctx, *, content: str):
    user_id = ctx.author.id
    now = datetime.now()
    last_time = last_report_time.get(user_id)
    if last_time and (now - last_time) < timedelta(hours=2):
        await ctx.send("レポートは2時間に1回のみ送信可能です。しばらく待ってから再度送信してください。")
        return
    last_report_time[user_id] = now
    async with aiohttp.ClientSession() as session:
        data = {
            "content": f"Report from {ctx.author.name}#{ctx.author.discriminator} (ID:{user_id}):\n{content}"
        }
        async with session.post(WEBHOOK_URL, json=data) as resp:
            if resp.status in (200, 204):
                await ctx.send("レポートを送信しました。ありがとうございました。")
            else:
                await ctx.send("レポートの送信に失敗しました。後ほど再度お試しください。")

# Bot起動完了時のログ
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

# メイン起動処理
if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")  # Koyebの環境変数にセットしてください
    if TOKEN is None:
        print("環境変数 DISCORD_BOT_TOKEN を設定してください")
    else:
        bot.run(TOKEN)
