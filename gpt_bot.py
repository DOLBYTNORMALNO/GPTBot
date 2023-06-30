import logging
import aiosqlite
import asyncio
from aiogram import Bot, Dispatcher, types, executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Command
from aiogram.contrib.middlewares.logging import LoggingMiddleware
import openai
from dotenv import load_dotenv
import os

load_dotenv()

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

openai.api_key = OPENAI_API_KEY
SECRET_PHRASE = os.getenv('SECRET_PHRASE')

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())
dp.middleware.setup(LoggingMiddleware())

db = None


async def on_startup(dp):
    logging.warning('Устанавливаем соединение с базой данных...')
    global db
    db = await aiosqlite.connect('users.db')
    cursor = await db.cursor()
    await cursor.execute('''CREATE TABLE IF NOT EXISTS users
                            (user_id INTEGER PRIMARY KEY, is_authorized INTEGER)''')
    await cursor.execute('''
        CREATE TABLE IF NOT EXISTS message_history
        (id INTEGER PRIMARY KEY, user_id INTEGER, message TEXT, is_bot INTEGER)''')
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
    try:
        await cursor.execute("INSERT INTO message_history (user_id, message, is_bot) VALUES (?, ?, 0)",
                            (user_id, user_text))
        await db.commit()
        await cursor.execute("SELECT message FROM message_history WHERE user_id=? AND is_bot=1 ORDER BY id DESC LIMIT 1",
                            (user_id,))
        last_bot_message = await cursor.fetchone()

        if last_bot_message is None:
            last_bot_message = ""
        else:
            last_bot_message = last_bot_message[0]

        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": last_bot_message},
                {"role": "assistant", "content": user_text},
            ]
        )
        bot_response = response['choices'][0]['message']['content']
        await cursor.execute("INSERT INTO message_history (user_id, message, is_bot) VALUES (?, ?, 1)",
                            (user_id, bot_response))
        await db.commit()
        await message.answer(bot_response)
    except openai.Error as e:
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
