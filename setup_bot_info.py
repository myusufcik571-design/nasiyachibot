import asyncio
from aiogram import Bot
from dotenv import load_dotenv
import os

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "8480888397:AAHx6CmWOkOqZlzKsaB_Zxng3ygBSylUahE")

async def set_commands():
    bot = Bot(token=BOT_TOKEN)
    
    # Short Description
    short_desc = "Nasiyalarim — Biznesingizning ishonchli, raqamli hisobchisi! Qarz daftari endi tarixda qoldi."
    await bot.set_my_short_description(short_desc)
    print("Short Description yangilandi.")
    
    # Full Description
    full_desc_plain = (
        "🚀 Nasiyalarim — Biznesingizni Keyingi Bosqichga Olib Chiqing!\n\n"
        "Eski qarz daftarlaridan va yo'qolgan pullardan charchadingizmi? "
        "Biz sizga zamonaviy va aniq yechim taklif qilamiz!\n\n"
        "💎 Imkoniyatlar:\n"
        "✅ Mijozlar Bazasi: Hisob-kitoblar kaftingizda.\n"
        "✅ Shaffoflik: Nasiya va to'lovlar tarixi.\n"
        "✅ Eslatmalar: Qarzdorlarga avtomatik xabar.\n"
        "✅ Hisobotlar: Excel formatida yuklab olish.\n"
        "✅ Nazorat: Xodimlar ishini kuzatib borish.\n\n"
        "Biznesingiz rivojiga hissa qo'shamiz! 😉\n\n"
        "👨‍💻 Admin: @xzzz911\n"
        "👇 Hoziroq /start ni bosing!"
    )

    await bot.set_my_description(full_desc_plain)
    print("Full Description yangilandi.")
    
    await bot.session.close()

if __name__ == "__main__":
    asyncio.run(set_commands())
