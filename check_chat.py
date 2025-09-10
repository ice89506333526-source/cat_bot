import asyncio
from aiogram import Bot

TOKEN = "8423035573:AAHo53sPuZJXbGXLhW5-EThbdXM5GrCULDQ"   # вставь свой
GROUP_ID = -1001234567890 # вставь свой

async def main():
    bot = Bot(TOKEN)
    try:
        chat = await bot.get_chat(GROUP_ID)
        print("✅ Доступ есть, инфо о чате:")
        print(chat)
    except Exception as e:
        print("❌ Ошибка:")
        print(e)

    await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
