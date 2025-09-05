import asyncio
from aiogram import Bot
import subprocess
import sys
import os

# ===== Настройки =====
API_TOKEN = "8423035573:AAHo53sPuZJXbGXLhW5-EThbdXM5GrCULDQ"
BOT_SCRIPT = "bot.py"  # Имя файла твоего бота
VENV_PATH = "venv\\Scripts\\python.exe"  # Путь к python в venv

async def clear_updates():
    bot = Bot(token=API_TOKEN)
    updates = await bot.get_updates(offset=-1)  # пропускаем все старые апдейты
    await bot.session.close()
    print(f"✅ Старые апдейты очищены, пропущено {len(updates)} апдейтов")

def start_bot():
    print("✅ Запуск бота...")
    subprocess.run([VENV_PATH, BOT_SCRIPT])

if __name__ == "__main__":
    # Шаг 1: очищаем апдейты
    asyncio.run(clear_updates())
    
    # Шаг 2: запускаем bot.py в том же окружении
    start_bot()
