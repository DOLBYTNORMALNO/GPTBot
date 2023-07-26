import logging
from aiogram import Bot, Dispatcher, types
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
SECRET_PHRASE = os.getenv('SECRET_PHRASE')

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Попытка установить ключи API и создать бота
try:
    bot = Bot(token=TELEGRAM_TOKEN)
    openai.api_key = OPENAI_API_KEY
except Exception as e:
    logging.error(f"Проблема с ключами API: {e}")
    raise e

# Настройка Memory storage
dp = Dispatcher(bot, storage=MemoryStorage())
dp.middleware.setup(LoggingMiddleware())


@dp.message_handler(commands='start')
async def start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await message.answer('Привет! Я бот, обученный на базе GPT-3.5. Пожалуйста, введите кодовую фразу для авторизации.')
    await state.set_state("auth")


@dp.message_handler(state="auth")
async def auth(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text

    if text == SECRET_PHRASE:
        await message.answer('Вы успешно авторизованы! Чем я могу помочь?')
        await state.set_state("chat")
    else:
        await message.answer('Извините, кодовая фраза неверна. Попробуйте снова.')


@dp.message_handler(state="chat")
async def respond(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_text = message.text

    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "assistant", "content": user_text},
        ]
    )
    bot_response = response['choices'][0]['message']['content']
    await message.answer(bot_response)


if __name__ == "__main__":
    from aiogram import executor

    executor.start_polling(dp)
