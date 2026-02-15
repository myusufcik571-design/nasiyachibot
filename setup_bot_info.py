import asyncio
from aiogram import Bot
from dotenv import load_dotenv
import os

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "8480888397:AAHx6CmWOkOqZlzKsaB_Zxng3ygBSylUahE")

async def set_commands():
    bot = Bot(token=BOT_TOKEN)
    
    # Short Description (seen before clicking Start)
    short_desc = "Nasiyalarim — Biznesingizning ishonchli, raqamli hisobchisi! Qarz daftari endi tarixda qoldi."
    await bot.set_my_short_description(short_desc)
    print("Short Description yangilandi.")
    
    # Full Description (seen when clicking bot handle or 'What can this bot do?')
    full_desc = (
        "🚀 <b>Nasiyalarim — Biznesingizni Keyingi Bosqichga Olib Chiqing!</b>\n\n"
        "Eski qarz daftarlaridan, tushunarsiz yozuvlardan va yo'qolgan pullardan charchadingizmi? "
        "Biz sizga <b>zamonaviy, xavfsiz va aniq</b> yechim taklif qilamiz!\n\n"
        "💎 <b>Botsiz Nima Qila Olasiz?</b>\n"
        "✅ <b>Mijozlar Bazasi:</b> Har bir mijozning hisob-kitobi kaftingizda.\n"
        "✅ <b>Shaffoflik:</b> Nasiya va to'lovlar tarixini istalgan vaqtda ko'ring.\n"
        "✅ <b>Avtomatik Eslatmalar:</b> Qarzdorlarga muloyim eslatma yuborishni botga qo'yib bering.\n"
        "✅ <b>Hisobotlar:</b> Kunlik va oylik savdoni Excel formatida yuklab oling.\n"
        "✅ <b>Xodimlar Nazorati:</b> Sotuvchilaringiz ishini osongina kuzatib boring.\n\n"
        "Siz biznesingizni rivojlantiring, hisob-kitobni bizga topshiring! 😉\n\n"
        "👨‍💻 <b>Dasturchi va Admin:</b> @xzzz911\n"
        "📞 <i>Taklif va murojaatlar uchun doim aloqadamiz!</i>\n\n"
        "👇 <b>Hoziroq /start ni bosing va sinab ko'ring!</b>"
    )
    # Note: set_my_description typically accepts plain text, but some formatting might be parsed or stripped depending on client. 
    # Usually it's plain text. HTML tags usually don't work in the 'Description' field shown before start.
    # Let's strip HTML for safety in the actual API call or rely on Telegram's handling (it usually shows plain text).
    # Actually, Description supports some entities but it's safer to use plain utf-8 symbols.
    
    full_desc_plain = (
        "🚀 Nasiyalarim — Biznesingizni Keyingi Bosqichga Olib Chiqing!\n\n"
        "Eski qarz daftarlaridan, tushunarsiz yozuvlardan va yo'qolgan pullardan charchadingizmi? "
        "Biz sizga zamonaviy, xavfsiz va aniq yechim taklif qilamiz!\n\n"
        "💎 Botsiz Nima Qila Olasiz?\n"
        "✅ Mijozlar Bazasi: Har bir mijozning hisob-kitobi kaftingizda.\n"
        "✅ Shaffoflik: Nasiya va to'lovlar tarixini istalgan vaqtda ko'ring.\n"
        "✅ Avtomatik Eslatmalar: Qarzdorlarga muloyim eslatma yuborishni botga qo'yib bering.\n"
        "✅ Hisobotlar: Kunlik va oylik savdoni Excel formatida yuklab oling.\n"
        "✅ Xodimlar Nazorati: Sotuvchilaringiz ishini osongina kuzatib boring.\n\n"
        "Siz biznesingizni rivojlantiring, hisob-kitobni bizga topshiring! 😉\n\n"
        "👨‍💻 Dasturchi va Admin: @xzzz911\n"
        "📞 Taklif va murojaatlar uchun doim aloqadamiz!\n\n"
        "👇 Hoziroq /start ni bosing va sinab ko'ring!"
    )

    await bot.set_my_description(full_desc_plain)
    print("Full Description yangilandi.")
    
    await bot.session.close()

if __name__ == "__main__":
    asyncio.run(set_commands())
