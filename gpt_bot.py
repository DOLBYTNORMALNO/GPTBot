import logging
import aiosqlite
import asyncio
from aiogram import Bot, Dispatcher, types, executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Command
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from base64 import b64encode, b64decode
import openai
from dotenv import load_dotenv
import os
from pytz import timezone
import datetime

load_dotenv()

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
AES_KEY = bytes.fromhex(os.getenv('AES_KEY'))
SECRET_PHRASE = os.getenv('SECRET_PHRASE')

openai.api_key = OPENAI_API_KEY

moscow_tz = timezone('Europe/Moscow')

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())
dp.middleware.setup(LoggingMiddleware())

db = None


def encrypt_message(message: str):
    cipher = AES.new(AES_KEY, AES.MODE_ECB)
    encrypted = cipher.encrypt(pad(message.encode(), AES.block_size))
    return encrypted.hex()


def decrypt_message(encrypted_message: str):
    cipher = AES.new(AES_KEY, AES.MODE_ECB)
    decrypted = unpad(cipher.decrypt(bytes.fromhex(encrypted_message)), AES.block_size)
    return decrypted.decode()


async def on_startup(dp):
    logging.warning('Устанавливаем соединение с базой данных...')
    global db
    db = await aiosqlite.connect('users.db')
    cursor = await db.cursor()
    await cursor.execute('''CREATE TABLE IF NOT EXISTS users
                            (user_id INTEGER PRIMARY KEY, is_authorized INTEGER)''')
    await cursor.execute('''
        CREATE TABLE IF NOT EXISTS message_history
        (id INTEGER PRIMARY KEY, user_id INTEGER, message TEXT, is_bot INTEGER, timestamp TIMESTAMP, is_encrypted INTEGER DEFAULT 0)''')
    await db.commit()

    await cursor.execute("PRAGMA table_info(message_history)")
    columns = await cursor.fetchall()
    column_names = [column[1] for column in columns]
    if "is_encrypted" not in column_names:
        await cursor.execute("ALTER TABLE message_history ADD COLUMN is_encrypted INTEGER DEFAULT 0")
    if "timestamp" not in column_names:
        await cursor.execute("ALTER TABLE message_history ADD COLUMN timestamp TIMESTAMP")
    await db.commit()

    # Зашифровка существующих сообщений
    await cursor.execute("SELECT id, message FROM message_history WHERE is_encrypted=0")
    messages = await cursor.fetchall()
    for message in messages:
        id, text = message
        encrypted_text = encrypt_message(text)
        await cursor.execute("UPDATE message_history SET message=?, is_encrypted=1 WHERE id=?", (encrypted_text, id))
    await db.commit()

    logging.warning('Соединение с базой данных установлено...')


async def on_shutdown(dp):
    logging.warning('Закрываем соединение с базой данных...')
    await db.close()
    await dp.storage.close()
    await dp.storage.wait_closed()
    logging.warning('Бот остановлен')


@dp.message_handler(commands='start')
async def start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    cursor = await db.cursor()
    await cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = await cursor.fetchone()

    if user is None:
        await cursor.execute("INSERT INTO users VALUES (?, 0)", (user_id,))
        await db.commit()
        await message.answer(
            'Привет! Я бот, обученный на базе GPT-3.5. Пожалуйста, введите кодовую фразу для авторизации.')
        await state.set_state("auth")

    elif user[1] == 0:
        await message.answer('Пожалуйста, введите кодовую фразу для авторизации.')
        await state.set_state("auth")

    else:
        await message.answer('Добро пожаловать обратно! Чем я могу помочь?')
        await state.set_state("chat")


@dp.message_handler(state="auth")
async def auth(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text
    cursor = await db.cursor()

    if text == SECRET_PHRASE:
        await cursor.execute("UPDATE users SET is_authorized=1 WHERE user_id=?", (user_id,))
        await db.commit()
        await message.answer('Вы успешно авторизованы! Чем я могу помочь?')
        await state.set_state("chat")

    else:
        await message.answer('Извините, кодовая фраза неверна. Попробуйте снова.')
        await state.set_state("auth")


async def send_message_to_all_users(message: str):
    cursor = await db.cursor()
    await cursor.execute("SELECT user_id FROM users")
    users = await cursor.fetchall()
    for user in users:
        try:
            await bot.send_message(user[0], message)
        except Exception as e:
            logging.error(f"Произошла ошибка при отправке сообщения пользователю {user[0]}: {e}")


async def stop_bot():
    logging.warning('Останавливаем бота...')
    await on_shutdown(dp)
    await bot.close()


@dp.message_handler(state="chat")
async def respond(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_text = message.text
    cursor = await db.cursor()
    timestamp = datetime.datetime.now(moscow_tz).strftime("%Y-%m-%d %H:%M:%S")
    try:
        encrypted_user_text = encrypt_message(user_text)
        await cursor.execute("INSERT INTO message_history (user_id, message, is_bot, timestamp, is_encrypted) VALUES (?, ?, 0, ?, 1)",
                             (user_id, encrypted_user_text, timestamp))
        await db.commit()
        await cursor.execute(
            "SELECT message FROM message_history WHERE user_id=? AND is_bot=1 ORDER BY id DESC LIMIT 1",
            (user_id,))
        last_bot_message = await cursor.fetchone()

        if last_bot_message is None:
            last_bot_message = ""
        else:
            last_bot_message = decrypt_message(last_bot_message[0])

        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": last_bot_message},
                {"role": "assistant", "content": user_text},
            ]
        )
        bot_response = response['choices'][0]['message']['content']
        encrypted_bot_response = encrypt_message(bot_response)
        await cursor.execute("INSERT INTO message_history (user_id, message, is_bot, timestamp, is_encrypted) VALUES (?, ?, 1, ?, 1)",
                             (user_id, encrypted_bot_response, timestamp))
        await db.commit()
        await message.answer(bot_response)
    except openai.error.OpenAIError as e:
        logging.error(f"Произошла ошибка при взаимодействии с API OpenAI: {e}")
        await send_message_to_all_users("Произошла ошибка при взаимодействии с API OpenAI. Бот временно недоступен.")
        await stop_bot()
        raise e
    except Exception as e:
        logging.error(f"Произошла неизвестная ошибка: {e}")
        await send_message_to_all_users("Произошла неизвестная ошибка. Бот временно недоступен.")
        await stop_bot()
        raise e


if __name__ == "__main__":
    from aiogram import executor

    executor.start_polling(dp, on_startup=on_startup, on_shutdown=on_shutdown)
