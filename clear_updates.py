from aiogram import Bot
import asyncio

API_TOKEN = "8423035573:AAHo53sPuZJXbGXLhW5-EThbdXM5GrCULDQ"  # вставьте токен вашего бота

async def main():
    bot = Bot(token=API_TOKEN)
    await bot.get_updates(offset=-1)  # пропускает все старые апдейты
    await bot.session.close()
    print("✅ Очередь апдейтов очищена")

asyncio.run(main())
