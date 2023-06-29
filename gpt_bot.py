import logging
import sqlite3
from aiogram import Bot, Dispatcher, types, executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Command
from aiogram.contrib.middlewares.logging import LoggingMiddleware
import openai
from dotenv import load_dotenv
import os

load_dotenv()

# Введите ваш токен Telegram Bot и ключ API OpenAI
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# Установка ключа API OpenAI
openai.api_key = OPENAI_API_KEY

# Кодовая фраза для авторизации
SECRET_PHRASE = os.getenv('SECRET_PHRASE')

# Создание соединения с базой данных SQLite
conn = sqlite3.connect('users.db', check_same_thread=False)
cursor = conn.cursor()

# Создание таблицы 'users', если она ещё не существует
cursor.execute('''CREATE TABLE IF NOT EXISTS users
             (user_id INTEGER PRIMARY KEY, is_authorized INTEGER)''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS message_history
    (id INTEGER PRIMARY KEY, user_id INTEGER, message TEXT, is_bot INTEGER)''')


# Настройка бота и диспетчера
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())
dp.middleware.setup(LoggingMiddleware())


@dp.message_handler(commands='start')
async def start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = cursor.fetchone()

    if user is None:
        # Новый пользователь, добавляем его в базу данных
        cursor.execute("INSERT INTO users VALUES (?, 0)", (user_id,))
        conn.commit()

        await message.answer(
            'Привет! Я бот, обученный на базе GPT-3.5. Пожалуйста, введите кодовую фразу для авторизации.')
        await state.set_state("auth")

    elif user[1] == 0:
        # Существующий пользователь, но еще не авторизован
        await message.answer('Пожалуйста, введите кодовую фразу для авторизации.')
        await state.set_state("auth")

    else:
        # Существующий и авторизованный пользователь
        await message.answer('Добро пожаловать обратно! Чем я могу помочь?')
        await state.set_state("chat")


@dp.message_handler(state="auth")
async def auth(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text

    if text == SECRET_PHRASE:
        # Правильная кодовая фраза, авторизуем пользователя
        cursor.execute("UPDATE users SET is_authorized=1 WHERE user_id=?", (user_id,))
        conn.commit()

        await message.answer('Вы успешно авторизованы! Чем я могу помочь?')
        await state.set_state("chat")

    else:
        # Неверная кодовая фраза
        await message.answer('Извините, кодовая фраза неверна. Попробуйте снова.')
        await state.set_state("auth")


@dp.message_handler(state="chat")
async def respond(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_text = message.text

    # Сохраняем сообщение пользователя в истории
    cursor.execute("INSERT INTO message_history (user_id, message, is_bot) VALUES (?, ?, 0)", (user_id, user_text))
    conn.commit()

    # Получаем последние 5 сообщений от пользователя
    cursor.execute("SELECT message FROM message_history WHERE user_id=? ORDER BY id DESC LIMIT 5", (user_id,))
    message_history = cursor.fetchall()

    # Формируем подсказку из истории сообщений
    prompt = " ".join([msg[0] for msg in reversed(message_history)])

    # Делаем запрос к модели GPT-3.5
    response = openai.Completion.create(
        engine="text-davinci-003",  # используем модель GPT-3.5
        prompt=prompt,
        temperature=0.5,
        max_tokens=1000
    )

    bot_response = response.choices[0].text.strip()

    # Сохраняем ответ бота в истории
    cursor.execute("INSERT INTO message_history (user_id, message, is_bot) VALUES (?, ?, 1)", (user_id, bot_response))
    conn.commit()

    await message.answer(bot_response)


@dp.message_handler(commands='cancel', state="*")
async def cancel(message: types.Message, state: FSMContext):
    user = message.from_user
    logging.info("Пользователь %s отменил разговор.", user.first_name)
    await message.answer('До свидания!')
    await state.finish()


if __name__ == '__main__':
    executor.start_polling(dp)
