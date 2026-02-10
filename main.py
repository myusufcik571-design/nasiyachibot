
import asyncio
from dotenv import load_dotenv
from keep_alive import keep_alive
import logging
from datetime import datetime, timedelta
import aiosqlite
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import openpyxl
import os
from aiogram.types import (
    FSInputFile,
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    CallbackQuery,
    ReplyKeyboardRemove
)
import hashlib
import re

# -----------------------------------------------------------------------------
# KONFIGURATSIYA
# -----------------------------------------------------------------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "8480888397:AAHx6CmWOkOqZlzKsaB_Zxng3ygBSylUahE")
# Agar Railway'da bo'lsak, DB_PATH environment variable orqali bazani ko'rsatamiz
# Default: shop_nasiya_v5.db (lokal kompyuter uchun)
DB_NAME = os.getenv("DB_PATH", "shop_nasiya_v5.db") 
REMINDER_TIME = "18:00"
ADMINS = [6104862378, 998999999] 
ADMIN_USERNAMES = ["xzzz911"] # Admin username (bot egasi)

logging.basicConfig(level=logging.INFO)
router = Router()

# -----------------------------------------------------------------------------
# MA'LUMOTLAR BAZASI
# -----------------------------------------------------------------------------
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                full_name TEXT,
                username TEXT,
                phone TEXT, 
                role TEXT,
                store_name TEXT,
                is_owner INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                locked_until TIMESTAMP
            )
        """)
        
        # Migration for existing table (if missing columns)
        try:
            await db.execute("ALTER TABLE users ADD COLUMN locked_until TIMESTAMP")
        except: pass
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seller_id INTEGER NOT NULL,
                full_name TEXT,
                phone TEXT,
                balance REAL DEFAULT 0,
                telegram_id INTEGER,
                FOREIGN KEY(seller_id) REFERENCES users(telegram_id),
                FOREIGN KEY(telegram_id) REFERENCES users(telegram_id)
            )
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                customer_id INTEGER,
                amount REAL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(customer_id) REFERENCES customers(id)
            )
        """)
        await db.commit()

        await db.commit()

# --- DB Metodlari ---

async def db_add_user(tg_id, name, username, role, phone=None, store_name=None, is_owner=0):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT OR REPLACE INTO users (telegram_id, full_name, username, role, phone, store_name, is_owner) 
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (tg_id, name, username, role, phone, store_name, is_owner))
        await db.commit()

async def db_get_user(tg_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM users WHERE telegram_id = ?", (tg_id,)) as cur:
            return await cur.fetchone()

async def db_get_user_id_by_phone(phone):
    async with aiosqlite.connect(DB_NAME) as db:
        short_phone = phone[-9:]
        async with db.execute("SELECT telegram_id FROM users WHERE phone LIKE ?", (f"%{short_phone}",)) as cur:
            res = await cur.fetchone()
            return res[0] if res else None

async def db_get_store_staff(store_name, exclude_tg_id):
    async with aiosqlite.connect(DB_NAME) as db:
        # Get employees of the same store, excluding the owner/current user
        async with db.execute("SELECT telegram_id, full_name, username, phone, created_at FROM users WHERE store_name = ? AND telegram_id != ? AND role = 'admin'", (store_name, exclude_tg_id)) as cur:
            return await cur.fetchall()

async def db_kick_staff(tg_id):
    async with aiosqlite.connect(DB_NAME) as db:
        # Reset user to buyer status, remove store access
        await db.execute("UPDATE users SET role = 'client', store_name = NULL, is_owner = 0 WHERE telegram_id = ?", (tg_id,))
        await db.commit()

async def db_add_customer(seller_id, name, phone, linked_tg_id=None):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id FROM customers WHERE seller_id = ? AND phone = ?", (seller_id, phone)) as cur:
            if await cur.fetchone(): return None
        
        async with db.execute("INSERT INTO customers (seller_id, full_name, phone, telegram_id) VALUES (?, ?, ?, ?)", 
                              (seller_id, name, phone, linked_tg_id)) as cur:
            await db.commit()
            return cur.lastrowid

async def db_link_customer(phone, tg_id):
    async with aiosqlite.connect(DB_NAME) as db:
        search_phone = f"%{phone[-9:]}"
        await db.execute("UPDATE customers SET telegram_id = ? WHERE phone LIKE ?", (tg_id, search_phone))
        await db.commit()

async def db_promote_to_staff(store_name, phone):
    async with aiosqlite.connect(DB_NAME) as db:
        short_phone = phone[-9:]
        # Find user by phone
        async with db.execute("SELECT telegram_id, role FROM users WHERE phone LIKE ?", (f"%{short_phone}",)) as cur:
            user = await cur.fetchone()
            
        if not user: return "not_found"
        if user[1] == 'admin': return "already_admin"
        if user[1] == 'blocked': return "blocked"
        
        # Promote
        await db.execute("UPDATE users SET role = 'admin', store_name = ?, is_owner = 0 WHERE telegram_id = ?", (store_name, user[0]))
        await db.commit()
        return user[0] # Return TG ID to notify

async def db_get_my_customers(seller_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, full_name, balance, telegram_id, phone FROM customers WHERE seller_id = ? ORDER BY full_name", (seller_id,)) as cur:
            return await cur.fetchall()

async def db_get_customer_if_mine(cust_id, seller_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, full_name, phone, balance, telegram_id FROM customers WHERE id = ? AND seller_id = ?", (cust_id, seller_id)) as cur:
            return await cur.fetchone()

async def db_get_customer_by_id(cust_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, full_name, phone, balance FROM customers WHERE id = ?", (cust_id,)) as cur:
            return await cur.fetchone()

async def db_get_buyer_debts(buyer_tg_id):
    async with aiosqlite.connect(DB_NAME) as db:
        sql = """
            SELECT u.store_name, c.balance, c.full_name, c.id
            FROM customers c
            JOIN users u ON c.seller_id = u.telegram_id
            WHERE c.telegram_id = ? AND c.balance != 0
        """
        async with db.execute(sql, (buyer_tg_id,)) as cur:
            return await cur.fetchall()

async def db_get_last_transactions(cust_id, limit=3):
    async with aiosqlite.connect(DB_NAME) as db:
        sql = "SELECT amount, description, created_at FROM transactions WHERE customer_id = ? ORDER BY created_at DESC LIMIT ?"
        async with db.execute(sql, (cust_id, limit)) as cur:
            return await cur.fetchall()

async def db_add_trans(cust_id, amount, desc):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO transactions (customer_id, amount, description) VALUES (?, ?, ?)", (cust_id, amount, desc))
        await db.execute("UPDATE customers SET balance = balance + ? WHERE id = ?", (amount, cust_id))
        await db.commit()

async def db_get_transactions_report(seller_id, days=None):
    async with aiosqlite.connect(DB_NAME) as db:
        sql = """
            SELECT t.created_at, c.full_name, c.phone, t.amount, t.description 
            FROM transactions t
            JOIN customers c ON t.customer_id = c.id
            WHERE c.seller_id = ?
        """
        params = [seller_id]
        if days:
            date_filter = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            sql += " AND t.created_at >= ?"
            params.append(date_filter)
        
        sql += " ORDER BY t.created_at DESC"
        async with db.execute(sql, tuple(params)) as cur:
            return await cur.fetchall()

async def db_get_store_total(seller_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT SUM(balance) FROM customers WHERE seller_id = ? AND balance > 0", (seller_id,)) as cur:
            res = await cur.fetchone()
            return res[0] if res[0] else 0

            res = await cur.fetchone()
            return res[0] if res[0] else 0

async def db_get_store_debtors(seller_id):
    async with aiosqlite.connect(DB_NAME) as db:
        sql = """
            SELECT telegram_id, full_name, balance, phone
            FROM customers 
            WHERE seller_id = ? AND balance > 0
        """
        async with db.execute(sql, (seller_id,)) as cur:
            return await cur.fetchall()

async def db_get_all_active_stores():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT telegram_id, store_name, full_name FROM users WHERE role = 'admin' AND is_owner = 1") as cur:
            return await cur.fetchall()

async def db_get_all_debtors_with_store():
    async with aiosqlite.connect(DB_NAME) as db:
        sql = """
            SELECT c.telegram_id, c.full_name, c.balance, u.store_name
            FROM customers c
            JOIN users u ON c.seller_id = u.telegram_id
            WHERE c.balance > 0 AND c.telegram_id IS NOT NULL
        """
        async with db.execute(sql) as cur:
            return await cur.fetchall()

async def db_get_all_users():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT full_name, username, phone, role, created_at FROM users") as cur:
            return await cur.fetchall()

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT full_name, username, phone, role, created_at FROM users") as cur:
            return await cur.fetchall()

async def db_get_users_by_role(role):
    async with aiosqlite.connect(DB_NAME) as db:
        # ID ni ham olamiz (telegram_id)
        async with db.execute("SELECT telegram_id, full_name, username, phone, created_at, store_name FROM users WHERE role = ? ORDER BY created_at DESC", (role,)) as cur:
            return await cur.fetchall()

async def db_get_blocked_users():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT telegram_id, full_name, username, phone, created_at, store_name FROM users WHERE role = 'blocked' ORDER BY created_at DESC") as cur:
            return await cur.fetchall()

async def db_search_customers(seller_id, query):
    async with aiosqlite.connect(DB_NAME) as db:
        # Search by name or phone
        # Normalize query?
        sql = """
            SELECT id, full_name, balance, telegram_id, phone 
            FROM customers 
            WHERE seller_id = ? AND (full_name LIKE ? OR phone LIKE ?) 
            ORDER BY full_name
        """
        like_query = f"%{query}%"
        async with db.execute(sql, (seller_id, like_query, like_query)) as cur:
            return await cur.fetchall()

async def get_store_owner_id(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT store_name, is_owner FROM users WHERE telegram_id = ?", (user_id,)) as cur:
            res = await cur.fetchone()
            if not res: return None
            store_name, is_owner = res
            
        if is_owner: return user_id
        
        # If staff, find the owner of this store
        async with db.execute("SELECT telegram_id FROM users WHERE store_name = ? AND is_owner = 1", (store_name,)) as cur:
            owner = await cur.fetchone()
            return owner[0] if owner else None

async def db_update_store_name(old_name, new_name):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET store_name = ? WHERE store_name = ?", (new_name, old_name))
        await db.commit()

async def db_update_user_phone(tg_id, new_phone):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET phone = ? WHERE telegram_id = ?", (new_phone, tg_id))
        await db.commit()

def clean_phone(phone):
    if not phone: return None
    return phone.replace('+', '').replace(' ', '').replace('-', '').replace('(', '').replace(')', '')

def format_phone_display(phone):
    if not phone: return "Yo'q"
    phone = str(phone).replace('+', '').replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
    if len(phone) == 9:
        return f"+998{phone}"
    return f"+{phone}"

# -----------------------------------------------------------------------------
# STATES & KEYBOARDS
# -----------------------------------------------------------------------------
class Form(StatesGroup):
    role = State()
    store_name_input = State()
    store_phone = State()
    phone = State()
    cust_name = State()
    cust_phone = State()
    debt_amt = State()
    debt_desc = State()
    pay_amt = State()
    pay_desc = State()
    msg_content = State()
    edit_name = State()
    block_id = State()
    
    # Registration
    reg_confirm = State()
    
    # Search
    search_query = State()
    
    # Broadcast
    broadcast_msg = State()

    # Seller Message
    seller_msg_target = State()
    seller_msg_content = State()

role_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="👨‍💼 Sotuvchi"), KeyboardButton(text="👤 Haridor")]], resize_keyboard=True)
shop_choice_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="🆕 Yangi do'kon ochish")],
    [KeyboardButton(text="⬅️ Orqaga")]
], resize_keyboard=True)

phone_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📞 Telefon raqamni yuborish", request_contact=True)]], resize_keyboard=True)

# Seller KB for OWNER (Has Cabinet)
seller_owner_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="➕ Mijoz qo'shish"), KeyboardButton(text="🔍 Mijoz Qidirish")],
    [KeyboardButton(text="💸 Nasiya yozish"), KeyboardButton(text="💰 To'lov qabul qilish")],
    [KeyboardButton(text="📤 Qarzdorga xabar"), KeyboardButton(text="📊 Hisobotlar")],
    [KeyboardButton(text="👥 A'zo odamlar"), KeyboardButton(text="🔐 Kabinet (Xodimlar)")],
    [KeyboardButton(text="📊 Balansni tekshirish")]
], resize_keyboard=True)

# Seller KB for STAFF (NO Cabinet)
seller_staff_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="➕ Mijoz qo'shish"), KeyboardButton(text="🔍 Mijoz Qidirish")],
    [KeyboardButton(text="💸 Nasiya yozish"), KeyboardButton(text="💰 To'lov qabul qilish")],
    [KeyboardButton(text="📤 Qarzdorga xabar"), KeyboardButton(text="📊 Hisobotlar")],
    [KeyboardButton(text="👥 A'zo odamlar"), KeyboardButton(text="📊 Balansni tekshirish")]
], resize_keyboard=True)

reports_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="📅 1 Haftalik (Excel)"), KeyboardButton(text="📅 1 Oylik (Excel)")],
    [KeyboardButton(text="📋 Barchasi (Excel)"), KeyboardButton(text="📈 Umumiy statistika")],
    [KeyboardButton(text="⬅️ Orqaga")]
], resize_keyboard=True)

# Cabinet KB
cabinet_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="👥 Xodimlar ro'yxati"), KeyboardButton(text="➕ Xodim qo'shish")],
    [KeyboardButton(text="✏️ Do'kon ma'lumotlarini o'zgartirish"), KeyboardButton(text="🔙 Orqaga")],
    [KeyboardButton(text="📞 Yordam (Admin)")]
], resize_keyboard=True)

owner_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="👨‍💼 Sotuvchilar Ro'yxati"), KeyboardButton(text="👤 Haridorlar Ro'yxati")],
    [KeyboardButton(text="🚫 Sotuvchini bloklash bo'limi"), KeyboardButton(text="📊 Hisobotlar")],
    [KeyboardButton(text="✉️ Sotuvchiga xabar"), KeyboardButton(text="📢 Xabar yuborish")]
], resize_keyboard=True)

block_menu_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="📋 Bloklangan Sotuvchilar"), KeyboardButton(text="➕ Yangi Sotuvchini Bloklash")],
    [KeyboardButton(text="🔙 Orqaga")]
], resize_keyboard=True)

buyer_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="💰 Mening qarzim"), KeyboardButton(text="🔄 Yangilash")]
], resize_keyboard=True)
cancel_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Bekor qilish")]], resize_keyboard=True)
confirm_reg_kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="✅ Tasdiqlash"), KeyboardButton(text="❌ Bekor qilish")]
], resize_keyboard=True)

# -----------------------------------------------------------------------------
# HANDLERS
# -----------------------------------------------------------------------------
@router.message(CommandStart())
async def start(msg: Message, state: FSMContext):
    await state.clear()
    user = await db_get_user(msg.from_user.id)
    if user:
        if user[4] == 'blocked':
            await msg.answer("⛔️ Sizning hisobingiz bloklangan.\nBotni qayta ishga tushirish uchun Bot Egasi (@xzzz911) bilan bog'laning.")
            return

        role_txt = f" ({user[5]})" if user[4] == 'admin' and user[5] else ""
        if user[4] == 'admin':
            is_owner = msg.from_user.id in ADMINS or (msg.from_user.username in ADMIN_USERNAMES)
            if is_owner: # Super Admin
                kb = owner_kb 
            else:
                # Check is_owner column (index 6 - after field removal)
                # Old index was 8, removed login/pass (2 fields), so new index is 6?
                # Let's check table structure:
                # 0:id, 1:fullname, 2:username, 3:phone, 4:role, 5:store_name, 6:is_owner
                kb = seller_owner_kb if user[6] else seller_staff_kb
        else:
            kb = buyer_kb
        await msg.answer(f"Xush kelibsiz, {user[1]}{role_txt}!", reply_markup=kb)
    else:
        is_owner_start = msg.from_user.id in ADMINS or (msg.from_user.username in ADMIN_USERNAMES)
        if is_owner_start:
            await db_add_user(msg.from_user.id, msg.from_user.full_name, msg.from_user.username, 'admin', None, "Bot Egasi", 1)
            await msg.answer("👑 Xush kelibsiz, Xo'jayin! Siz maxsus admin menyusidasiz.", reply_markup=owner_kb)
        else:
            await msg.answer("Assalomu alaykum! Tanlang:", reply_markup=role_kb)
            await state.set_state(Form.role)

@router.message(F.text == "❌ Bekor qilish")
async def cancel_all(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("Bekor qilindi", reply_markup=ReplyKeyboardRemove())
    await start(msg, state)

@router.message(Form.role)
async def set_role(msg: Message, state: FSMContext):
    if msg.text == "👨‍💼 Sotuvchi":
        await msg.answer("Tanlang:", reply_markup=shop_choice_kb)
        # We stay in state Form.role to handle the next choice? 
        # Actually it's better to create a new state or just handle text.
        # Let's clear state so we can handle the text in a general handler or keep it?
        # The next handler checks F.text.
        await state.clear() 
    elif msg.text == "👤 Haridor":
        await msg.answer("Telefon raqamingizni yuboring:", reply_markup=phone_kb)
        await state.set_state(Form.phone)

# General handler for Shop Choice or Back
@router.message(F.text.in_({"🆕 Yangi do'kon ochish", "⬅️ Orqaga"}))
async def shop_choice_handler(msg: Message, state: FSMContext):
    if msg.text == "⬅️ Orqaga":
        await start(msg, state)
    elif msg.text == "🆕 Yangi do'kon ochish":
        await msg.answer("🏪 Yangi do'kon nomini kiriting:", reply_markup=cancel_kb)
        await state.set_state(Form.store_name_input)



# --- REGISTER FLOW ---
@router.message(Form.store_name_input)
async def save_store(msg: Message, state: FSMContext):
    store_name = msg.text.strip()
    if len(store_name) < 3:
        await msg.answer("⚠️ Do'kon nomi kamida 3 harf bo'lishi kerak. Qayta kiriting:", reply_markup=cancel_kb)
        return
        
    await state.update_data(store_name=store_name)
    await msg.answer("📞 Do'kon uchun aloqa raqamini kiriting (yoki pastdagi tugmani bosing):", 
                     reply_markup=ReplyKeyboardMarkup(keyboard=[
                         [KeyboardButton(text="📞 Raqamni yuborish", request_contact=True)],
                         [KeyboardButton(text="➡️ O'tkazib yuborish")],
                         [KeyboardButton(text="❌ Bekor qilish")]
                     ], resize_keyboard=True))
    await state.set_state(Form.store_phone)

@router.message(Form.store_phone)
async def save_store_phone(msg: Message, state: FSMContext):
    phone = None
    if msg.contact: phone = msg.contact.phone_number
    elif msg.text and msg.text not in ["➡️ O'tkazib yuborish", "✅ Yetarli (Tugatish)", "❌ Bekor qilish"]: phone = msg.text
    
    if phone:
        phone = clean_phone(phone)
        data = await state.get_data()
        existing_phones = data.get('phones', [])
        existing_phones.append(phone)
        await state.update_data(phones=existing_phones)
        
        await msg.answer("✅ Raqam qo'shildi! Yana raqam qo'shasizmi?", reply_markup=ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="📞 Yana raqam qo'shish", request_contact=True)],
            [KeyboardButton(text="✅ Yetarli (Tugatish)")]
        ], resize_keyboard=True))
        return

    data = await state.get_data()
    store_name = data.get('store_name')
    phones_list = data.get('phones', [])
    
    # Format phones for display
    phones_str = ", ".join(phones_list) if phones_list else "Kiritilmadi"
    
    # Confirmation Text
    # Confirmation Text
    confirm_text = (
        "📝 <b>Ma'lumotlarni tasdiqlang:</b>\n\n"
        f"🏪 <b>Do'kon:</b> {store_name}\n"
        f"📞 <b>Tel:</b> {phones_str}\n\n"
        "Barcha ma'lumotlar to'g'rimi?"
    )
    
    await msg.answer(confirm_text, parse_mode="HTML", reply_markup=confirm_reg_kb)
    await state.set_state(Form.reg_confirm)

@router.message(Form.reg_confirm)
async def reg_complete(msg: Message, state: FSMContext):
    if msg.text == "✅ Tasdiqlash":
        data = await state.get_data()
        store_name = data.get('store_name')
        phones_list = data.get('phones', [])
        phones_str = ", ".join(phones_list) if phones_list else None
        
        await db_add_user(msg.from_user.id, msg.from_user.full_name, msg.from_user.username, 'admin', phones_str, store_name, 1)
        
        # Decide KB
        if msg.from_user.id in ADMINS or msg.from_user.username in ADMIN_USERNAMES:
             kb = owner_kb
        else:
             kb = seller_owner_kb
             
        await msg.answer(f"✅ <b>Tabriklaymiz!</b>\n\n'{store_name}' do'koni muvaffaqiyatli ro'yxatdan o'tdi!", reply_markup=kb, parse_mode="HTML")
        await state.clear()
    else:
        # If they type something else, or press cancel (Cancel is handled by global handler, but if they type something strictly)
        # Global handler @router.message(F.text == "❌ Bekor qilish") handles explicit cancel.
        # But if they type random text:
        await msg.answer("Iltimos, tugmalardan birini tanlang.", reply_markup=confirm_reg_kb)

@router.message(Form.phone, F.contact)
async def save_buyer(msg: Message, state: FSMContext):
    phone = clean_phone(msg.contact.phone_number)
    await db_add_user(msg.from_user.id, msg.from_user.full_name, msg.from_user.username, 'client', phone, None)
    await db_link_customer(phone, msg.from_user.id) 
    await msg.answer("✅ Rahmat! Siz muvaffaqiyatli ro'yxatdan o'tdingiz.", reply_markup=buyer_kb)
    await state.clear()

async def ensure_seller(msg: Message):
    user = await db_get_user(msg.from_user.id)
    if not user:
        await msg.answer("Ro'yxatdan o'tmagansiz.")
        return False
    
    if user[4] == 'blocked':
        await msg.answer("⛔️ Siz bloklangansiz. Bot egasi bilan bog'laning: @xzzz911")
        return False

    if user[4] != 'admin':
        await msg.answer("Siz sotuvchi emassiz.")
        return False
    return True

@router.message(F.text == "➕ Mijoz qo'shish")
async def add_c(msg: Message, state: FSMContext):
    if not await ensure_seller(msg): return
    await msg.answer("Mijoz ismi:", reply_markup=cancel_kb)
    await state.set_state(Form.cust_name)

@router.message(Form.cust_name)
async def save_c_name(msg: Message, state: FSMContext):
    await state.update_data(name=msg.text)
    await msg.answer("Telefon raqami (raqamlar):")
    await state.set_state(Form.cust_phone)

@router.message(Form.cust_phone)
async def save_c_phone(msg: Message, state: FSMContext):
    data = await state.get_data()
    phone = clean_phone(msg.text)
    
    if not phone or not phone.isdigit():
        await msg.answer("⚠️ Iltimos, to'g'ri telefon raqam kiriting (faqat raqamlar).")
        return

    seller_id = await get_store_owner_id(msg.from_user.id)
    if not seller_id:
        await msg.answer("⚠️ Do'kon egasi topilmadi (Tizim xatosi).", reply_markup=seller_kb)
        await state.clear()
        return

    linked_tg_id = await db_get_user_id_by_phone(phone)
    res = await db_add_customer(seller_id, data['name'], phone, linked_tg_id)
    
    link_status = "🔗 (Botga ulangan)" if linked_tg_id else "⚪️ (Botga ulanmagan)"
    phone_f = format_phone_display(phone)
    if res:
        await msg.answer(f"✅ Mijoz qo'shildi!\n👤 <b>{data['name']}</b>\n📞 {phone_f}\n{link_status}", reply_markup=seller_kb, parse_mode="HTML")
    else:
        await msg.answer("⚠️ Bu mijoz allaqachon mavjud.", reply_markup=seller_kb)
    await state.clear()

async def get_my_cust_kb(seller_id, prefix):
    custs = await db_get_my_customers(seller_id)
    if not custs: return None
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for c in custs:
        marker = "🟢" if c[3] else "⚪️"
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"{marker} {c[1]} | {c[4]} ({c[2]:,.0f})", callback_data=f"{prefix}_{c[0]}")])
    return kb

@router.message(F.text == "💸 Nasiya yozish")
async def debt_start(msg: Message, state: FSMContext):
    if not await ensure_seller(msg): return
    store_owner_id = await get_store_owner_id(msg.from_user.id)
    kb = await get_my_cust_kb(store_owner_id, "debt")
    if kb: await msg.answer("Kimga?", reply_markup=kb)
    else: await msg.answer("Mijozlar yo'q")

@router.callback_query(F.data.startswith("debt_"))
async def debt_sel(call: CallbackQuery, state: FSMContext):
    cid = int(call.data.split("_")[1])
    await state.update_data(cid=cid)
    await call.message.delete()
    await call.message.answer("Summa:", reply_markup=cancel_kb)
    await state.set_state(Form.debt_amt)

@router.message(Form.debt_amt)
async def debt_save(msg: Message, state: FSMContext):
    try:
        amt = float(msg.text)
        await state.update_data(amt=amt)
        await msg.answer("Izoh:")
        await state.set_state(Form.debt_desc)
    except: await msg.answer("Raqam yozing")

@router.message(Form.debt_desc)
async def debt_fin(msg: Message, state: FSMContext):
    d = await state.get_data()
    store_owner_id = await get_store_owner_id(msg.from_user.id)
    cust = await db_get_customer_if_mine(d['cid'], store_owner_id) # Verify ownership
    if cust:
        await db_add_trans(d['cid'], d['amt'], msg.text)
        
        # Notify user (if linked)
        if cust[4]: 
            try:
                user = await db_get_user(msg.from_user.id)
                store_name = user[5] if user else "Do'kon"
                timestamp = datetime.now().strftime("%d.%m.%Y %H:%M")
                await msg.bot.send_message(cust[4], 
                    f"💸 <b>Sizga nasiya yozildi!</b>\n\n"
                    f"🏪 <b>{store_name}</b>\n"
                    f"💰 <b>Summa:</b> {d['amt']:,.0f} so'm\n"
                    f"📝 <b>Izoh:</b> {msg.text}\n"
                    f"📅 {timestamp}\n\n"
                    f"<i>Batafsil ma'lumot uchun botga kiring: @nasiyambot</i>", parse_mode="HTML")
            except: pass
            
        await msg.answer(f"✅ <b>Nasiya Muvaffaqiyatli Yozildi!</b>\n\n👤 <b>Mijoz:</b> {cust[1]}\n💰 <b>Summa:</b> {d['amt']:,.0f} so'm\n📝 <b>Izoh:</b> {msg.text}", reply_markup=seller_kb, parse_mode="HTML")
    else:
        await msg.answer("⚠️ Xatolik: Mijoz topilmadi.", reply_markup=seller_kb)
    await state.clear()

@router.message(F.text == "💰 To'lov qabul qilish")
async def pay_start(msg: Message, state: FSMContext):
    if not await ensure_seller(msg): return
    store_owner_id = await get_store_owner_id(msg.from_user.id)
    kb = await get_my_cust_kb(store_owner_id, "pay")
    if kb: await msg.answer("Kimdan to'lov qabul qilamiz?", reply_markup=kb)

@router.callback_query(F.data.startswith("pay_"))
async def pay_sel(call: CallbackQuery, state: FSMContext):
    cid = int(call.data.split("_")[1])
    await state.update_data(cid=cid)
    await call.message.delete()
    await call.message.answer("Summa:", reply_markup=cancel_kb)
    await state.set_state(Form.pay_amt)

@router.message(Form.pay_amt)
async def pay_save(msg: Message, state: FSMContext):
    try:
        amt = float(msg.text)
        await state.update_data(amt=amt)
        await msg.answer("Izoh:", reply_markup=cancel_kb)
        await state.set_state(Form.pay_desc)
    except: await msg.answer("Raqam yozing")

@router.message(Form.pay_desc)
async def pay_fin(msg: Message, state: FSMContext):
    d = await state.get_data()
    store_owner_id = await get_store_owner_id(msg.from_user.id)
    cust = await db_get_customer_if_mine(d['cid'], store_owner_id)
    if cust:
        await db_add_trans(d['cid'], -d['amt'], msg.text)
        
        # Notify user implicitly handled by next checking balance or explicit msg? 
        # Usually payment notification is good.
        if cust[4]:
             try:
                user = await db_get_user(msg.from_user.id)
                store_name = user[5] if user else "Do'kon"
                timestamp = datetime.now().strftime("%d.%m.%Y %H:%M")
                await msg.bot.send_message(cust[4], 
                    f"✅ <b>To'lov qabul qilindi!</b>\n\n"
                    f"🏪 <b>{store_name}</b>\n"
                    f"💰 <b>Summa:</b> {d['amt']:,.0f} so'm\n"
                    f"📝 <b>Izoh:</b> {msg.text}\n"
                    f"📅 {timestamp}\n\n"
                    f"<i>Sizning to'lovingiz uchun rahmat!</i>", parse_mode="HTML")
             except: pass

        new_balance = cust[3] - d['amt']
        await msg.answer(f"✅ <b>To'lov Muvaffaqiyatli Qabul Qilindi!</b>\n\n👤 <b>Mijoz:</b> {cust[1]}\n💰 <b>To'landi:</b> {d['amt']:,.0f} so'm\n📉 <b>Qoldiq Qarz:</b> {new_balance:,.0f} so'm", reply_markup=seller_kb, parse_mode="HTML")
    await state.clear()

@router.message(F.text == "📊 Balansni tekshirish")
async def check_start(msg: Message):
    if not await ensure_seller(msg): return
    store_owner_id = await get_store_owner_id(msg.from_user.id)
    kb = await get_my_cust_kb(store_owner_id, "check")
    if kb: await msg.answer("Kimni?", reply_markup=kb)

@router.callback_query(F.data.startswith("check_"))
async def check_show(call: CallbackQuery):
    cid = int(call.data.split("_")[1])
    cust = await db_get_customer_by_id(cid) 
    trans = await db_get_last_transactions(cid, 5) # Show last 5
    
    text = f"👤 <b>MIJOZ KARTOCHKASI</b>\n\n"
    text += f"🏷 <b>Ism:</b> {cust[1]}\n"
    text += f"📞 <b>Tel:</b> {format_phone_display(cust[2])}\n"
    
    balance_status = "� QARZDOR" if cust[3] > 0 else "🟢 QARZI YO'Q" if cust[3] == 0 else "🔵 HAQDOR"
    text += f"�💰 <b>Holat:</b> {balance_status}\n"
    text += f"💵 <b>Balans:</b> {cust[3]:,.0f} so'm\n\n"
    
    if trans:
        text += "📜 <b>Oxirgi 5 ta amaliyot:</b>\n"
        for t_amt, t_desc, t_date in trans:
            d = t_date[5:16] if t_date else "" # MM-DD HH:MM
            if t_amt > 0:
                text += f"➖ <b>{t_desc}</b>: {t_amt:,.0f} <i>({d})</i>\n"
            else:
                text += f"➕ To'lov: {-t_amt:,.0f} <i>({d})</i>\n"
    else:
        text += "<i>Amaliyotlar tarixi bo'sh.</i>"
        
    # Actions
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Nasiya Yozish", callback_data=f"debt_{cid}"), InlineKeyboardButton(text="💰 To'lov Qabul Qilish", callback_data=f"pay_{cid}")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_to_list")] # Handling this would need "back_to_list" handler logic or just delete msg
    ])
    
    await call.message.delete()
    await call.message.answer(text, parse_mode="HTML", reply_markup=kb)

@router.message(F.text == "🔍 Mijoz Qidirish")
async def search_cmd(msg: Message, state: FSMContext):
    if not await ensure_seller(msg): return
    await msg.answer("🔍 Qidirilayotgan mijoz ismini yoki telefon raqamini kiriting:", reply_markup=cancel_kb)
    await state.set_state(Form.search_query)

@router.message(Form.search_query)
async def search_handle(msg: Message, state: FSMContext):
    query = msg.text.strip()
    if not await ensure_seller(msg): return
    
    store_owner_id = await get_store_owner_id(msg.from_user.id)
    customers = await db_search_customers(store_owner_id, query)
    
    if not customers:
        await msg.answer(f"⚠️ '{query}' bo'yicha hech kim topilmadi.\nQayta urinib ko'ring yoki Bekor qiling.", reply_markup=cancel_kb)
        return

    # Use existing keyboard logic? Or similar
    kb_builder = []
    for c in customers:
        # c = (id, users_full_name, balance, telegram_id, phone)
        # We need callback data like "check_{cid}"
        btn_text = f"{c[1]} | {c[2]:,.0f}"
        kb_builder.append([InlineKeyboardButton(text=btn_text, callback_data=f"check_{c[0]}")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=kb_builder)
    
    await msg.answer(f"🔍 <b>Qidiruv natijalari:</b>\n\nTopildi: {len(customers)} ta mijoz.", reply_markup=kb, parse_mode="HTML")
    # Finish state? Or keep searching? 
    # Usually better to clear state so they can use buttons.
    # But if they want to search again? 
    # Let's clear state to allow KB interaction logic to flow normally.
    await state.clear()
    
    # Restore main menu? No, keep it clean.
    # But user might want Main Menu back.
    # Let's verify ensure_seller restored KB? No.
    # We should probably send meaningful message.
    pass 

@router.message(F.text == "📊 Hisobotlar")
async def reports_menu(msg: Message):
    if not await ensure_seller(msg): return
    await msg.answer("Hisobot turini tanlang:", reply_markup=reports_kb)

@router.message(F.text == "⬅️ Orqaga")
async def back_to_main(msg: Message):
    user = await db_get_user(msg.from_user.id)
    if not user: return

    # Super Admin logic
    if msg.from_user.id in ADMINS or (msg.from_user.username in ADMIN_USERNAMES):
         await msg.answer("Xo'jayin menyusi:", reply_markup=owner_kb)
         return

    # Store Staff/Owner logic
    if user[4] == 'admin':
        kb = seller_owner_kb if user[6] else seller_staff_kb
        await msg.answer("Bosh menyu:", reply_markup=kb)
    else:
        await msg.answer("Bosh menyu:", reply_markup=buyer_kb)

@router.message(F.text == "📈 Umumiy statistika")
async def report(msg: Message):
    if not await ensure_seller(msg): return
    store_owner_id = await get_store_owner_id(msg.from_user.id)
    total = await db_get_store_total(store_owner_id)
    
    # Get debtors count
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM customers WHERE seller_id = ? AND balance > 0", (store_owner_id,)) as cur:
            debtors_count = (await cur.fetchone())[0]
            
    text = (f"📊 <b>DO'KON STATISTIKASI</b>\n\n"
            f"💰 <b>Umumiy Nasiya:</b> {total:,.0f} so'm\n"
            f"👥 <b>Qarzdorlar soni:</b> {debtors_count} ta\n"
            f"📅 Sana: {datetime.now().strftime('%d.%m.%Y')}")
            
    await msg.answer(text, reply_markup=reports_kb, parse_mode="HTML")

async def generate_excel(data, filename):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Hisobot"
    ws.append(["Sana", "Mijoz", "Telefon", "Summa", "Izoh", "Tur"])
    
    for row in data:
        dt, name, phone, amt, desc = row
        trans_type = "Nasiya (+)" if amt > 0 else "To'lov (-)"
        ws.append([dt, name, phone, amt, desc, trans_type])
        
    wb.save(filename)
    return filename

@router.message(F.text.in_({"📅 1 Haftalik (Excel)", "📅 1 Oylik (Excel)", "📋 Barchasi (Excel)"}))
async def send_excel_report(msg: Message):
    if not await ensure_seller(msg): return
    
    days = None
    if "1 Haftalik" in msg.text: days = 7
    elif "1 Oylik" in msg.text: days = 30
    
    store_owner_id = await get_store_owner_id(msg.from_user.id)
    data = await db_get_transactions_report(store_owner_id, days)
    if not data:
        await msg.answer("Bu davr uchun ma'lumot yo'q.")
        return
        
    await msg.answer("📁 Fayl tayyorlanmoqda...")
    filename = f"hisobot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    await generate_excel(data, filename)
    
    file = FSInputFile(filename)
    await msg.answer_document(file, caption=f"📊 Hisobot: {msg.text}")
    
    os.remove(filename)

@router.message(F.text == "📤 Qarzdorga xabar")
async def msg_start(msg: Message, state: FSMContext):
    if not await ensure_seller(msg): return
    store_owner_id = await get_store_owner_id(msg.from_user.id)
    async with aiosqlite.connect(DB_NAME) as db:
        sql = "SELECT telegram_id, full_name, balance FROM customers WHERE seller_id = ? AND balance > 0 AND telegram_id IS NOT NULL"
        async with db.execute(sql, (store_owner_id,)) as cur:
            debtors = await cur.fetchall()
            
    if not debtors:
        await msg.answer("Telegramli qarzdorlar yo'q")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for d in debtors:
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"{d[1]} ({d[2]:,.0f})", callback_data=f"sendmsg_{d[0]}")])
    await msg.answer("Kimga?", reply_markup=kb)

@router.callback_query(F.data.startswith("sendmsg_"))
async def msg_sel(call: CallbackQuery, state: FSMContext):
    tg_id = int(call.data.split("_")[1])
    await state.update_data(target=tg_id)
    await call.message.delete()
    await call.message.answer("Xabar (Text/Voice):", reply_markup=cancel_kb)
    await state.set_state(Form.msg_content)

@router.message(Form.msg_content)
async def msg_send(msg: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    user = await db_get_user(msg.from_user.id)
    store_name = user[5] 
    try:
        if msg.text:
            await bot.send_message(d['target'], f"✉️ <b>{store_name}</b>:\n{msg.text}", parse_mode="HTML")
        elif msg.voice:
            await bot.send_voice(d['target'], msg.voice.file_id, caption=f"📞 <b>{store_name}</b>", parse_mode="HTML")
        await msg.answer("Yuborildi!", reply_markup=seller_kb)
    except: await msg.answer("Xatolik")
    await state.clear()

@router.message(F.text == "👥 A'zo odamlar")
async def members_start(msg: Message):
    if not await ensure_seller(msg): return
    store_owner_id = await get_store_owner_id(msg.from_user.id)
    kb = await get_my_cust_kb(store_owner_id, "member")
    if kb: await msg.answer("A'zolar (🟢=Ulangan, ⚪️=Ulanmagan):", reply_markup=kb)
    else: await msg.answer("Hozircha a'zolar yo'q.")

@router.callback_query(F.data.startswith("member_"))
async def member_show(call: CallbackQuery):
    cid = int(call.data.split("_")[1])
    store_owner_id = await get_store_owner_id(call.from_user.id)
    cust = await db_get_customer_if_mine(cid, store_owner_id)
    if not cust:
        await call.answer("Mijoz topilmadi")
        return
    info = (f"👤 <b>Ism:</b> {cust[1]}\n"
            f"📞 <b>Tel:</b> {format_phone_display(cust[2])}\n"
            f"💰 <b>Balans:</b> {cust[3]:,.0f} so'm\n"
            f"🔗 <b>Telegram ulangan:</b> {'✅' if cust[4] else '❌'}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Ismni tahrirlash", callback_data=f"editname_{cid}")],
        [InlineKeyboardButton(text="🗑️ O'chirish (Agar qarzi bo'lmasa)", callback_data=f"delcust_{cid}")]
    ])
    await call.message.edit_text(info, reply_markup=kb, parse_mode="HTML")

class EditNameState(StatesGroup):
    waiting_for_new_name = State()

class EditStoreState(StatesGroup):
    waiting_for_store_name = State()
    waiting_for_store_phone = State()
    waiting_for_staff_phone = State()

@router.callback_query(F.data.startswith("editname_"))
async def edit_name_start(call: CallbackQuery, state: FSMContext):
    cid = int(call.data.split("_")[1])
    await state.update_data(cid=cid)
    await call.message.delete()
    await call.message.answer("Yangi ismni kiriting:", reply_markup=cancel_kb)
    await state.set_state(EditNameState.waiting_for_new_name)

@router.message(EditNameState.waiting_for_new_name)
async def edit_name_save(msg: Message, state: FSMContext):
    d = await state.get_data()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE customers SET full_name = ? WHERE id = ?", (msg.text, d['cid']))
        await db.commit()
    await msg.answer(f"✅ Ism o'zgartirildi: {msg.text}", reply_markup=seller_kb)
    await state.clear()

@router.callback_query(F.data.startswith("delcust_"))
async def delete_customer(call: CallbackQuery):
    cid = int(call.data.split("_")[1])
    store_owner_id = await get_store_owner_id(call.from_user.id)
    cust = await db_get_customer_if_mine(cid, store_owner_id)
    if not cust:
        await call.answer("Xatolik: Mijoz topilmadi")
        return
    if cust[3] != 0:
        await call.answer("❌ O'chirib bo'lmaydi! Mijozning qarzi yoki haqi bor.", show_alert=True)
        return
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM customers WHERE id = ?", (cid,))
        await db.execute("DELETE FROM transactions WHERE customer_id = ?", (cid,))
        await db.commit()
    await call.message.delete()
    await call.message.answer(f"✅ Mijoz ({cust[1]}) <b>butunlay o'chirildi.</b>", parse_mode="HTML", reply_markup=seller_kb)

@router.message(F.text == "👨‍💼 Sotuvchilar Ro'yxati")
async def show_sellers_list(msg: Message):
    if not await ensure_seller(msg): return
    is_owner = msg.from_user.id in ADMINS or (msg.from_user.username in ADMIN_USERNAMES)
    if not is_owner:
        await msg.answer("⛔️ Bu bo'lim faqat Bot Egasi (@xzzz911) uchun!")
        return

    sellers = await db_get_users_by_role('admin')
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    text = f"🏪 <b>Sotuvchilar Ro'yxati ({len(sellers)}):</b>\n\n"
    for idx, s in enumerate(sellers, 1):
        # s: telegram_id, full_name, username, phone, created_at, store_name
        phone_f = format_phone_display(s[3])
        status = "🟢"
        row_text = (f"{idx}. {status} <b>{s[1]}</b>\n"
                    f"   ID: <code>{s[0]}</code> | 🏠 {s[5]}\n")
        text += row_text
        
        # Add Block button for each
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=f"🚫 Bloklash", callback_data=f"preblock_{s[0]}")
        ])
    
    await msg.answer(text, parse_mode="HTML", reply_markup=kb)

@router.message(F.text == "👤 Haridorlar Ro'yxati")
async def show_buyers_list(msg: Message):
    if not await ensure_seller(msg): return
    is_owner = msg.from_user.id in ADMINS or (msg.from_user.username in ADMIN_USERNAMES)
    if not is_owner:
        await msg.answer("⛔️ Bu bo'lim faqat Bot Egasi (@xzzz911) uchun!")
        return

    buyers = await db_get_users_by_role('client')
    text = f"👤 <b>Haridorlar Ro'yxati ({len(buyers)}):</b>\n\n"
    for idx, b in enumerate(buyers, 1):
         # b: telegram_id, full_name, username, phone, created_at, store_name
        tg_username = f"@{b[2]}" if b[2] else "Yo'q"
        phone_f = format_phone_display(b[3])
        text += (f"🆔 <b>ID:</b> <code>{b[0]}</code>\n"
                 f"👤 <b>Ism:</b> {b[1]}\n"
                 f"🔗 <b>Username:</b> {tg_username}\n"
                 f"📞 <b>Tel:</b> {phone_f}\n"
                 f"🕒 <b>Sana:</b> {b[4]}\n"
                 f"➖➖➖➖➖➖➖➖\n")

    if len(text) > 4000:
        for x in range(0, len(text), 4000):
            await msg.answer(text[x:x+4000], parse_mode="HTML")
    else:
        await msg.answer(text, parse_mode="HTML")

@router.message(F.text == "🚫 Sotuvchini bloklash bo'limi")
async def block_menu(msg: Message):
    if not await ensure_seller(msg): return
    is_owner = msg.from_user.id in ADMINS or (msg.from_user.username in ADMIN_USERNAMES)
    if not is_owner: return
    await msg.answer("🚫 Bloklash Bo'limi:", reply_markup=block_menu_kb)

@router.message(F.text == "🔙 Orqaga")
async def back_to_main_menu_consolidated(msg: Message):
    if not await ensure_seller(msg): return
    is_owner = msg.from_user.id in ADMINS or (msg.from_user.username in ADMIN_USERNAMES)
    
    user = await db_get_user(msg.from_user.id)
    # If user is admin (owner or staff)
    if user and user[4] == 'admin':
         # If super admin
        if is_owner:
            kb = owner_kb
        # If Store Owner
        elif user[6]: 
            kb = seller_owner_kb
        # If Staff
        else:
            kb = seller_staff_kb
    else:
        kb = buyer_kb
        
    await msg.answer("Asosiy menyu:", reply_markup=kb)

@router.message(F.text == "📋 Bloklangan Sotuvchilar")
async def show_blocked_list(msg: Message):
    if not await ensure_seller(msg): return
    is_owner = msg.from_user.id in ADMINS or (msg.from_user.username in ADMIN_USERNAMES)
    if not is_owner: return

    blocked = await db_get_blocked_users()
    if not blocked:
        await msg.answer("🚫 Bloklangan foydalanuvchilar yo'q.")
        return

    text = f"🚫 <b>Bloklangan Foydalanuvchilar ({len(blocked)}):</b>\n\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    
    for idx, b in enumerate(blocked, 1):
        tg_username = f"@{b[2]}" if b[2] else "Yo'q"
        phone_f = format_phone_display(b[3])
        text += (f"🆔 <b>ID:</b> <code>{b[0]}</code>\n"
                 f"👤 <b>Ism:</b> {b[1]}\n"
                 f"🔗 <b>Username:</b> {tg_username}\n"
                 f"📞 <b>Tel:</b> {phone_f}\n"
                 f"➖➖➖➖➖➖➖➖\n")
        
        # Add unblock button for each user
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"🔓 Blokdan ochish: {b[1]}", callback_data=f"unblock_{b[0]}")])

    await msg.answer(text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data.startswith("preblock_"))
async def pre_block_user(call: CallbackQuery):
    target_id = int(call.data.split("_")[1])
    target_user = await db_get_user(target_id)
    if not target_user:
        await call.answer("Foydalanuvchi topilmadi")
        return
        
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ HA, Bloklansin", callback_data=f"do_block_{target_id}")],
        [InlineKeyboardButton(text="❌ Yo'q, Bekor qilish", callback_data="cancel_block")]
    ])
    await call.message.answer(f"⚠️ <b>Diqqat!</b>\n\nSiz rostdan ham <b>{target_user[1]}</b> (ID: {target_id}) ni bloklamoqchimisiz?\n\nBu foydalanuvchi botdan foydalana olmay qoladi.", reply_markup=confirm_kb, parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "cancel_block")
async def cancel_block_action(call: CallbackQuery):
    await call.message.delete()
    await call.answer("Bekor qilindi")

@router.callback_query(F.data.startswith("do_block_"))
async def perform_block(call: CallbackQuery, bot: Bot):
    target_id = int(call.data.split("_")[1])
    
    if target_id == call.from_user.id:
        await call.answer("O'zingizni bloklay olmaysiz!", show_alert=True)
        return

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET role = 'blocked' WHERE telegram_id = ?", (target_id,))
        await db.commit()
    
    try:
        await bot.send_message(target_id, "⛔️ <b>DIQQAT!</b>\n\nSizning hisobingiz Bot Administratori tomonidan bloklandi.\nQayta tiklash uchun @xzzz911 ga murojaat qiling.", parse_mode="HTML")
    except: pass
    
    await call.message.edit_text(f"✅ Foydalanuvchi (ID: {target_id}) muvaffaqiyatli bloklandi.")

@router.callback_query(F.data.startswith("unblock_"))
async def unblock_user_handler(call: CallbackQuery, bot: Bot):
    target_id = int(call.data.split("_")[1])
    
    async with aiosqlite.connect(DB_NAME) as db:
        # Reset to 'client'. They must login again to regain admin/seller access. 
        # This is safer than guessing if they were admin or owner.
        await db.execute("UPDATE users SET role = 'client' WHERE telegram_id = ?", (target_id,))
        await db.commit()
    
    try:
        await bot.send_message(target_id, "✅ <b>Xushxabar!</b>\n\nSiz blokdan ochildingiz. Botdan foydalanish uchun qayta kirishingiz mumkin (/start).", parse_mode="HTML")
    except:
        pass
        
    await call.answer("✅ Foydalanuvchi blokdan ochildi!", show_alert=True)
    await call.message.delete()
    await call.message.answer(f"✅ ID {target_id} blokdan chiqarildi va Oddiy Foydalanuvchi (Client) ga o'tkazildi.\nAgar u sotuvchi bo'lsa, qayta login qilishi kerak.")

@router.message(F.text == "🔐 Kabinet (Xodimlar)")
async def cabinet_entry(msg: Message):
    user = await db_get_user(msg.from_user.id)
    if not user or not user[6]: # Check is_owner
         return
    await msg.answer("🗄 Kabinet Menyu:", reply_markup=cabinet_kb)


# @router.message(F.text == "🔙 Orqaga") -> Consolidated above



@router.message(F.text == "👥 Xodimlar ro'yxati")
async def show_cabinet_staff(msg: Message):
    user = await db_get_user(msg.from_user.id)
    if not user or not user[6]: 
        await msg.answer("Ruxsat yo'q.")
        return

    store_owner_id = await get_store_owner_id(msg.from_user.id)
    # Get staff for this store
    staff = await db_get_store_staff(user[5], msg.from_user.id)
    if not staff:
        await msg.answer("Sizning do'koningizda boshqa ulangan xodimlar yo'q.", reply_markup=cabinet_kb)
        return

    text = f"👥 <b>Do'kon Xodimlari ({len(staff)}):</b>\n\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    
    for s in staff:
        tg_username = f"@{s[2]}" if s[2] else "Yo'q"
        phone_f = format_phone_display(s[3])
        text += (f"👤 <b>Ism:</b> {s[1]}\n"
                 f"🔗 <b>Username:</b> {tg_username}\n"
                 f"📞 <b>Tel:</b> {phone_f}\n"
                 f"🕒 <b>Ulandi:</b> {s[4]}\n"
                 f"➖➖➖➖➖➖➖➖\n")
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"❌ Chiqarib yuborish: {s[1]}", callback_data=f"kick_{s[0]}")])

    await msg.answer(text, reply_markup=kb, parse_mode="HTML")

@router.message(F.text == "📞 Yordam (Admin)")
async def cabinet_help(msg: Message):
    user = await db_get_user(msg.from_user.id)
    if not user or not user[6]: return
    
    await msg.answer("🆘 <b>Yordam Markazi</b>\n\nBot bo'yicha savol yoki muammolar bo'lsa, Bot Adminiga murojaat qiling:\n\n👨‍💻 <b>Admin:</b> @xzzz911", parse_mode="HTML")

@router.message(F.text == "✏️ Do'kon ma'lumotlarini o'zgartirish")
async def edit_store_menu(msg: Message):
    user = await db_get_user(msg.from_user.id)
    if not user or not user[6]: # Check is_owner
        return
        
    store_name = user[5]
    phone = user[3]
    phone_f = format_phone_display(phone)
    
    text = (f"🏪 <b>Do'kon Sozlamalari</b>\n\n"
            f"🏷 <b>Nomi:</b> {store_name}\n"
            f"📞 <b>Tel:</b> {phone_f}\n\n"
            f"Nimani o'zgartirmoqchisiz?")
            
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Nomni o'zgartirish", callback_data="edit_store_name")],
        [InlineKeyboardButton(text="✏️ Telefonni o'zgartirish", callback_data="edit_store_phone")]
    ])
    
    await msg.answer(text, reply_markup=kb, parse_mode="HTML")

    await msg.answer(text, reply_markup=kb, parse_mode="HTML")

@router.message(F.text == "➕ Xodim qo'shish")
async def add_staff_start(msg: Message, state: FSMContext):
    user = await db_get_user(msg.from_user.id)
    if not user or not user[6]: # Check is_owner
         return
    await msg.answer("👤 Yangi xodimning telefon raqamini yuboring (yoki yozing):\n\n<i>Eslatma: Xodim avval botga kirib, ro'yxatdan o'tgan bo'lishi kerak.</i>", reply_markup=phone_kb)
    await state.set_state(EditStoreState.waiting_for_staff_phone)

@router.message(EditStoreState.waiting_for_staff_phone)
async def add_staff_save(msg: Message, state: FSMContext, bot: Bot):
    phone = None
    if msg.contact: phone = msg.contact.phone_number
    elif msg.text: phone = msg.text
    
    if not phone:
        await msg.answer("Iltimos, to'g'ri raqam kiriting.")
        return

    phone = clean_phone(phone)
    user = await db_get_user(msg.from_user.id) # Owner
    store_name = user[5]
    
    result = await db_promote_to_staff(store_name, phone)
    
    if result == "not_found":
        await msg.answer("⚠️ Foydalanuvchi topilmadi. U avval botga kirib (/start), oddiy foydalanuvchi sifatida ro'yxatdan o'tishi kerak.", reply_markup=cabinet_kb)
    elif result == "already_admin":
        await msg.answer("⚠️ Bu foydalanuvchi allaqachon boshqa do'konda admin yoki sotuvchi.", reply_markup=cabinet_kb)
    elif result == "blocked":
        await msg.answer("⚠️ Bu foydalanuvchi bloklangan.", reply_markup=cabinet_kb)
    else:
        # Success (result is TG ID)
        await msg.answer(f"✅ <b>Xodim Muvaffaqiyatli Qo'shildi!</b>\n\nEndi u <b>{store_name}</b> do'koniga kirish huquqiga ega.", parse_mode="HTML", reply_markup=cabinet_kb)
        try:
            await bot.send_message(result, f"🎉 <b>Tabriklaymiz!</b>\n\nSiz <b>{store_name}</b> do'koniga Xodim sifatida qo'shildingiz!\nBotdan to'liq foydalanishingiz mumkin.", parse_mode="HTML", reply_markup=seller_staff_kb)
        except: pass
    
    await state.clear()

@router.callback_query(F.data == "edit_store_name")
async def edit_store_name_start(call: CallbackQuery, state: FSMContext):
    await call.message.delete()
    await call.message.answer("🏪 Yangi do'kon nomini kiriting:", reply_markup=cancel_kb)
    await state.set_state(EditStoreState.waiting_for_store_name)

@router.message(EditStoreState.waiting_for_store_name)
async def edit_store_name_save(msg: Message, state: FSMContext):
    new_name = msg.text.strip()
    if len(new_name) < 3:
        await msg.answer("⚠️ Do'kon nomi kamida 3 harf bo'lishi kerak.")
        return
        
    user = await db_get_user(msg.from_user.id)
    if user:
        old_name = user[5]
        await db_update_store_name(old_name, new_name)
        await msg.answer(f"✅ Do'kon nomi o'zgartirildi: <b>{new_name}</b>", parse_mode="HTML", reply_markup=cabinet_kb)
    else:
        await msg.answer("Xatolik.")
    await state.clear()

@router.callback_query(F.data == "edit_store_phone")
async def edit_store_phone_start(call: CallbackQuery, state: FSMContext):
    await call.message.delete()
    await call.message.answer("📞 Yangi telefon raqamini yuboring (yoki yozing):", reply_markup=phone_kb) # Reusing phone_kb from registration
    await state.set_state(EditStoreState.waiting_for_store_phone)

@router.message(EditStoreState.waiting_for_store_phone)
async def edit_store_phone_save(msg: Message, state: FSMContext):
    phone = None
    if msg.contact: phone = msg.contact.phone_number
    elif msg.text: phone = msg.text
    
    if phone:
        phone = clean_phone(phone)
        await db_update_user_phone(msg.from_user.id, phone) # Update owner phone
        
        # Also link any customers if needed? Usually registration does that.
        # But here just updating contact info.
        
        await msg.answer(f"✅ Telefon raqam yangilandi: <b>{format_phone_display(phone)}</b>", parse_mode="HTML", reply_markup=cabinet_kb)
        await state.clear()
    else:
        await msg.answer("Iltimos, to'g'ri raqam kiriting.")

# --- CHANGE LOGIN/PASS FLOW ---


@router.callback_query(F.data.startswith("kick_"))
async def kick_staff_handler(call: CallbackQuery, bot: Bot):
    kick_id = int(call.data.split("_")[1])
    
    # Verify owner permission again
    owner = await db_get_user(call.from_user.id)
    if not owner or not owner[6]:
        await call.answer("Huquqingiz yo'q", show_alert=True)
        return

    await db_kick_staff(kick_id)
    await db_kick_staff(kick_id)
    try:
        await bot.send_message(kick_id, 
            "😔 <b>Afsuski, xayrlashamiz...</b>\n\n"
            "Siz do'kondan chiqarib yuborildingiz va tizimga kirish huquqingiz to'xtatildi.\n"
            "<i>Kelajakdagi ishlaringizda omad tilaymiz!</i> 👋", parse_mode="HTML")
    except:
        pass
    
    await call.message.delete()
    await call.answer("✅ Xodim chiqarib yuborildi!", show_alert=True)
    await call.message.answer("✅ Xodim ro'yxatdan o'chirildi.")

@router.message(Form.block_id)
async def block_user_save(msg: Message, state: FSMContext, bot: Bot):
    try:
        target_id = int(msg.text)
    except:
        await msg.answer("ID raqam bo'lishi kerak.")
        return

    # Check validity
    if target_id == msg.from_user.id:
        await msg.answer("O'zingizni bloklay olmaysiz!")
        return
        
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET role = 'blocked' WHERE telegram_id = ?", (target_id,))
        await db.commit()
    
    # Notify user
    try:
        await bot.send_message(target_id, "⛔️ Sizning hisobingiz bloklandi.\nQayta tiklash uchun bot egasi (@xzzz911) bilan bog'laning.")
        await msg.answer(f"✅ ID {target_id} bloklandi va xabar yuborildi.", reply_markup=owner_kb)
    except:
        await msg.answer(f"✅ ID {target_id} bloklandi (lekin xabar yuborilmadi - bot bloklangan bo'lishi mumkin).", reply_markup=owner_kb)
    
    await state.clear()

@router.message(F.text == "💰 Mening qarzim")
async def my_debts(msg: Message):
    user = await db_get_user(msg.from_user.id)
    if user and user[4] == 'blocked':
        await msg.answer("⛔️ Siz bloklangansiz.")
        return

@router.callback_query(F.data == "notify_all_debtors")
async def notify_all_debtors_handler(call: CallbackQuery, bot: Bot):
    store_owner_id = await get_store_owner_id(call.from_user.id)
    if not store_owner_id:
        await call.answer("Do'kon egasi topilmadi", show_alert=True)
        return

    debtors = await db_get_store_debtors(store_owner_id)
    if not debtors:
        await call.answer("Xushxabar: Qarzdorlar yo'q! 🎉", show_alert=True)
        return
        
    user = await db_get_user(call.from_user.id) # Sender (could be staff or owner)
    store_name = user[5] if user else "Bizning Do'kon"
    
    count = 0
    await call.message.edit_text("⏳ <i>Xabarlar yuborilmoqda...</i>", parse_mode="HTML")
    
    for d in debtors:
        tg_id, name, balance, phone = d
        if not tg_id: continue # Skip if no telegram linked
        
        try:
            msg_text = (f"🌸 <b>Assalomu alaykum, {name}!</b>\n\n"
                        f"Sizga <b>{store_name}</b> do'konidan muhim eslatma:\n\n"
                        f"Kichik qarzdorlik mavjud: <b>{balance:,.0f} so'm</b> 📉\n\n"
                        f"<i>Imkoningiz bo'lganda to'lov qilsangiz, biz juda xursand bo'lar edik.</i>\n"
                        f"Siz bilan ishlashdan mamnunmiz! 😊")
            await bot.send_message(tg_id, msg_text, parse_mode="HTML")
            count += 1
            await asyncio.sleep(0.5) # Anti-flood
        except: pass
        
    await call.message.delete()
    await call.message.answer(
        f"✅ <b>Xabarlar Muvaffaqiyatli Yetkazildi!</b>\n\n"
        f"📨 Jami <b>{count} nafar</b> mijozga ushbu muloyim eslatma yuborildi.\n"
        f"<i>Ishlaringizga rivoj tilaymiz!</i> 🚀", parse_mode="HTML")

async def send_daily_debtor_report(bot: Bot):
    owners = await db_get_all_active_stores()
    for owner_id, store_name, owner_name in owners:
        debtors = await db_get_store_debtors(owner_id)
        if not debtors: continue
        
        total_debt = sum(d[2] for d in debtors)
        debtors_count = len(debtors)
        
        text = (f"🌙 <b>XAYRLI KECH, {owner_name}!</b>\n\n"
                f"Siz uchun <b>{store_name}</b> do'konining bugungi qisqacha hisoboti:\n\n"
                f"📊 <b>Faol Qarzdorlar:</b> {debtors_count} kishi\n"
                f"💰 <b>Umumiy Qarz Summasi:</b> {total_debt:,.0f} so'm\n\n"
                f"💡 <i>Mijozlaringizga chiroyli eslatma yuborishni xohlaysizmi?</i>\n"
                f"Pastdagi tugmani bosing, biz hammasini o'zimiz bajaramiz.")
                
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"✨ Barchasiga Eslatma Yuborish ({debtors_count})", callback_data="notify_all_debtors")]
        ])
        
        try:
            await bot.send_message(owner_id, text, reply_markup=kb, parse_mode="HTML")
        except: pass

@router.message(F.text == "💰 Mening qarzim")
async def my_debts(msg: Message):
    user = await db_get_user(msg.from_user.id)
    if user and user[4] == 'blocked':
        await msg.answer("⛔️ Siz bloklangansiz.")
        return

    debts = await db_get_buyer_debts(msg.from_user.id)
    if not debts:
        await msg.answer("🎉 Sizda hech qaysi do'kondan qarz yo'q!")
        return
    text = "📋 <b>Sizning qarzlar ro'yxatingiz:</b>\n\n"
    total_all = 0
    async with aiosqlite.connect(DB_NAME) as db:
        for store_name, balance, _, cust_id in debts:
            if balance == 0: continue
            seller_phone_res = await db.execute("SELECT u.phone FROM customers c JOIN users u ON c.seller_id = u.telegram_id WHERE c.id = ?", (cust_id,))
            async with seller_phone_res as cur:
                s_row = await cur.fetchone()
                store_contact = format_phone_display(s_row[0]) if s_row and s_row[0] else "Mavjud emas"
            status = "🔴 Qarz" if balance > 0 else "🟢 Haq"
            text += f"🏪 <b>{store_name}</b>\n"
            text += f"📞 Aloqa: {store_contact}\n"
            text += f"└ {status}: {balance:,.0f} so'm\n"
            trans = await db_get_last_transactions(cust_id, 10)
            if trans:
                text += "   <i>So'nggi amaliyotlar:</i>\n"
                for t_amt, t_desc, t_date in trans:
                    d = t_date[:10] if t_date else ""
                    if t_amt > 0:
                         text += f"   • {t_desc}: {t_amt:,.0f} <i>({d})</i>\n"
                    else:
                         text += f"   • To'lov: {-t_amt:,.0f} <i>({d})</i>\n"
            text += "\n"
            total_all += balance
    text += f"▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
    text += f"📊 <b>Jami balans:</b> {total_all:,.0f} so'm"
    await msg.answer(text, parse_mode="HTML")

@router.message(F.text == "🔄 Yangilash")
async def refresh(msg: Message):
    await my_debts(msg)


async def send_reminders(bot: Bot):
    # This function might be redundant if we rely on daily reports, but keeping it as requested logic backup
    # Making it polite too
    debtors = await db_get_all_debtors_with_store()
    for tg_id, name, balance, store in debtors:
        try:
            msg = (f"🌸 <b>Assalomu alaykum, {name}!</b>\n\n"
                   f"Sizning <b>{store}</b> do'konidan <b>{balance:,.0f} so'm</b> qarzingiz bor ekan.\n"
                   f"<i>Iltimos, vaqtingiz bo'lganda xabar oling. Rahmat!</i> 😊")
            await bot.send_message(tg_id, msg, parse_mode="HTML")
            await asyncio.sleep(0.5)
        except: pass

async def check_subscriptions(bot: Bot):
    async with aiosqlite.connect(DB_NAME) as db:
        # Get all active admins (sellers/owners) who are not blocked
        async with db.execute("SELECT telegram_id, full_name, created_at, phone, store_name FROM users WHERE role = 'admin'") as cur:
            users = await cur.fetchall()

    for u in users:
        tg_id, name, created_at_str, phone, store_name = u
        
        # Parse created_at
        try:
            # created_at format: YYYY-MM-DD HH:MM:SS (default sqlite)
            created_dt = datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")
        except:
            continue # Skip if date format is wrong
            
        now = datetime.now()
        
        # Calculate subscription end (1 month after creation, then 2 months, etc.)
        # Logic: If today is the same day of the month as creation day
        # But simplistic approach: Check if (now - created_at).days % 30 == 0? 
        # Better: Store 'subscription_end' date. But requested "har oyning shu sanasida".
        # So if created 10th Feb, check on 10th March, 10th April...
        
        # Check if today is the "payment day"
        # We need to check if today's day matches created_at day.
        # AND we must ensure we haven't already blocked them or notified them today?
        # The prompt says "avtomatik bloklansin". 
        # Let's assume the user has a 1 month trial/subscription from creation.
        # If we want RECURRING payment, we need to track "last_payment_date".
        # Default behavior based on request: "Har oyning shu sanasida u avtomatik bloklansin"
        # This implies they are blocked EVERY MONTH until they pay?
        # Yes. So every month on day X, they get blocked.
        
        if now.day == created_dt.day and now.date() != created_dt.date(): # processing only if not registration day
             # It is the day!
             # We should block them.
             
             # Block user
             async with aiosqlite.connect(DB_NAME) as db:
                 await db.execute("UPDATE users SET role = 'blocked' WHERE telegram_id = ?", (tg_id,))
                 await db.commit()
                 
             # Notify User
             try:
                 await bot.send_message(tg_id, 
                     f"⛔️ <b>DIQQAT! OYLIK TO'LOV VAQTI KELDI</b>\n\n"
                     f"Hurmatli <b>{name}</b>,\n"
                     f"Sizning botdan foydalanish muddatingiz tugadi (Bugun sana: {now.strftime('%d.%m.%Y')}).\n"
                     f"Xizmatdan foydalanishni davom ettirish uchun Bot Egasi bilan bog'laning va to'lovni amalga oshiring.\n\n"
                     f"📞 <b>Admin:</b> @xzzz911", parse_mode="HTML")
             except: pass
             
             # Notify Admin (Owner)
             for admin_id in ADMINS:
                 try:
                     await bot.send_message(admin_id,
                         f"💸 <b>TO'LOV VAQTI KELDI! (AUTO-BLOCK)</b>\n\n"
                         f"👤 <b>Mijoz:</b> {name}\n"
                         f"🏪 <b>Do'kon:</b> {store_name}\n"
                         f"📞 <b>Aloqa:</b> {phone}\n"
                         f"⏳ <b>Ro'yxatdan o'tgan:</b> {created_at_str}\n\n"
                         f"🛑 <b>Status:</b> Tizim tomonidan BLOKLANDI.\n"
                         f"<i>To'lov qabul qilingach, \"Bloklash bo'limi\" orqali faollashtiring.</i>", parse_mode="HTML")
                 except: pass

async def send_backup(bot: Bot):
    try:
        # Send DB file to the Main Admin (first one in ADMINS)
        super_admin_id = ADMINS[0] 
        backup_file = FSInputFile(DB_NAME)
        caption = (f"💾 <b>RAQAMLI OLTIN ZAXIRANGIZ</b>\n\n"
                   f"📅 <b>Sana:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
                   f"🔒 <b>Hajmi:</b> {os.path.getsize(DB_NAME) / 1024:.2f} KB\n\n"
                   f"<i>Ma'lumotlaringiz xavfsiz joyda!</i> 🛡")
        
        await bot.send_document(super_admin_id, backup_file, caption=caption, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Backup error: {e}")

async def scheduler(bot: Bot):
    sent_reminders = False
    checked_subs = False
    sent_backup = False
    
    while True:
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        
        # New 20:00 Logic (Daily Report)
        if current_time == "20:00":
            if not sent_reminders:
                await send_daily_debtor_report(bot)
                sent_reminders = True
        else:
            sent_reminders = False
            
        # Subscription Check (09:00)
        if current_time == "09:00":
             if not checked_subs:
                 await check_subscriptions(bot)
                 checked_subs = True
        else:
            checked_subs = False

        # Backup (23:00)
        if current_time == "23:00":
            if not sent_backup:
                await send_backup(bot)
                sent_backup = True
        else:
            sent_backup = False
            
        await asyncio.sleep(30)

async def main():
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    asyncio.create_task(scheduler(bot))
    print("Bot v8.3 (Clean Rebuild) ishga tushdi...")
    await dp.start_polling(bot)

@router.message(F.text == "📢 Xabar yuborish")
async def broadcast_start(msg: Message, state: FSMContext):
    is_owner = msg.from_user.id in ADMINS or (msg.from_user.username in ADMIN_USERNAMES)
    if not is_owner: return
    await msg.answer("📢 Barcha foydalanuvchilarga yuboriladigan xabarni kiriting (Rasm, Video yoki Matn):", reply_markup=cancel_kb)
    await state.set_state(Form.broadcast_msg)

@router.message(Form.broadcast_msg)
async def broadcast_send(msg: Message, state: FSMContext, bot: Bot):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT telegram_id FROM users") as cur:
            users = await cur.fetchall()
    
    count = 0
    status_msg = await msg.answer("⏳ Xabar yuborilmoqda...")
    for u in users:
        try:
            if msg.text:
                await bot.send_message(u[0], msg.text)
            elif msg.photo:
                await bot.send_photo(u[0], msg.photo[-1].file_id, caption=msg.caption)
            elif msg.video:
                await bot.send_video(u[0], msg.video.file_id, caption=msg.caption)
            count += 1
            await asyncio.sleep(0.05)
        except:
            pass
    
    await status_msg.delete()
    await msg.answer(f"✅ Xabar {count} ta foydalanuvchiga muvaffaqiyatli yuborildi!", reply_markup=owner_kb)
    await state.clear()

@router.message(F.text == "✉️ Sotuvchiga xabar")
async def seller_msg_start(msg: Message, state: FSMContext):
    is_owner = msg.from_user.id in ADMINS or (msg.from_user.username in ADMIN_USERNAMES)
    if not is_owner: return
    
    sellers = await db_get_users_by_role('admin')
    if not sellers:
        await msg.answer("Sotuvchilar yo'q.")
        return
        
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for s in sellers:
        # s: telegram_id, full_name, ...
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"✉️ {s[1]} ({s[5]})", callback_data=f"selmsg_{s[0]}")])
    
    await msg.answer("Qaysi sotuvchiga xabar yubormoqchisiz?", reply_markup=kb)

@router.callback_query(F.data.startswith("selmsg_"))
async def seller_msg_sel(call: CallbackQuery, state: FSMContext):
    target_id = int(call.data.split("_")[1])
    await state.update_data(target=target_id)
    await call.message.delete()
    await call.message.answer("Sotuvchi uchun xabarni kiriting (Matn/Rasm/Video):", reply_markup=cancel_kb)
    await state.set_state(Form.seller_msg_content)

@router.message(Form.seller_msg_content)
async def seller_msg_send(msg: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    target_id = d['target']
    
    try:
        prefix = "✉️ <b>ADMINSTRATORdan Xabar:</b>\n\n"
        if msg.text:
            await bot.send_message(target_id, prefix + msg.text, parse_mode="HTML")
        elif msg.photo:
            await bot.send_photo(target_id, msg.photo[-1].file_id, caption=prefix + (msg.caption or ""), parse_mode="HTML")
        elif msg.video:
            await bot.send_video(target_id, msg.video.file_id, caption=prefix + (msg.caption or ""), parse_mode="HTML")
        elif msg.voice:
            await bot.send_voice(target_id, msg.voice.file_id, caption=prefix + "Ovozli xabar", parse_mode="HTML")
            
        await msg.answer("✅ Xabar sotuvchiga yuborildi!", reply_markup=owner_kb)
    except Exception as e:
        await msg.answer(f"❌ Xabar yuborishda xatolik: {e}", reply_markup=owner_kb)
    
    await state.clear()

if __name__ == "__main__":
    try: 
        keep_alive() # Start web server for uptime monitors
        asyncio.run(main())
    except: pass
