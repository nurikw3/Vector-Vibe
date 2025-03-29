import asyncio
import logging

from aiogram import Bot, Dispatcher

from config_reader import config
from handlers import cmds
from callbacks import user_cb
from keyboards.builders import inline_builder
from orm.db import User, Track, AsyncSessionLocal, init_db, reset_database


async def main() -> None:
    # Инициализация базы данных
    await init_db()
    
    bot = Bot(config.BOT_TOKEN.get_secret_value())
    dp = Dispatcher()

    dp.include_routers(
        cmds.router,
        user_cb.router
    )
    
    await bot.send_message(
        chat_id=config.ADMIN_ID, 
        text='🔰 Бот включен!!', 
        reply_markup=inline_builder(['❌ Удалить оповещение'], ['del'])
    )

    await bot.delete_webhook(True)
    try:
        await dp.start_polling(bot)
    finally:
        # Закрываем соединение с базой перед завершением
        await AsyncSessionLocal.close()

try:
    if __name__ == '__main__':
        logging.basicConfig(level=logging.INFO, filename="logs.log",filemode="w",
                        format="%(asctime)s %(levelname)s %(message)s")
        logging.info("Bot activate!")
        print("run!")
        asyncio.run(main())
except KeyboardInterrupt:
    logging.info("Bot force stopped!")
    print('stop!')
    exit()
    