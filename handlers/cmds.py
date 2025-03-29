from aiogram import Router, F, types
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, CommandStart
from sqlalchemy.future import select
from config_reader import config
from keyboards import builders
from yandex import YandexMusicSDK
from orm.db import AsyncSessionLocal, User, Track  
from sqlalchemy import func

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

router = Router()

async def get_user_by_chat_id(chat_id: int):
    """Получаем объект User по chat_id."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.chat_id == chat_id))
        return result.scalars().first()

async def get_user_by_username(username: str):
    """Получаем объект User по username."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.username == username))
        return result.scalars().first()

async def get_user_tracks(user_id: int):
    """Получаем MFCC-треки пользователя."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Track.mfcc, Track.artist).where(Track.user_id == user_id))
        return result.fetchall()  # [(mfcc, artist), ...]
    
async def get_user_track_count(user_id: int) -> int:
    """Возвращает количество треков пользователя."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(func.count()).where(Track.user_id == user_id))
        return result.scalar() or 0  # Если нет треков, вернёт 0

# def calculate_mean_mfcc(tracks):
#     """Вычисляем средний MFCC-вектор по артистам."""
#     if not tracks:
#         return np.zeros(20)

#     artist_mfcc = {}
#     for mfcc, artist in tracks:
#         mfcc_vector = np.frombuffer(mfcc, dtype=np.float32).reshape(20, -1).mean(axis=1)
#         artist_mfcc.setdefault(artist, []).append(mfcc_vector)

#     mean_vectors = [np.mean(vectors, axis=0) for vectors in artist_mfcc.values()]
#     return np.mean(mean_vectors, axis=0) if mean_vectors else np.zeros(20)

def calculate_mean_mfcc(tracks):
    """Вычисляет усредненный MFCC-вектор + дисперсию, чтобы учитывать разнообразие."""
    if not tracks:
        return np.zeros(40)  # Удваиваем размерность (среднее + дисперсия)

    artist_mfcc = {}
    for mfcc, artist in tracks:
        mfcc_vector = np.frombuffer(mfcc, dtype=np.float32).reshape(20, -1).mean(axis=1)
        artist_mfcc.setdefault(artist, []).append(mfcc_vector)

    mean_vectors = [np.mean(vectors, axis=0) for vectors in artist_mfcc.values()]
    var_vectors = [np.var(vectors, axis=0) for vectors in artist_mfcc.values()]

    mean_mfcc = np.mean(mean_vectors, axis=0) if mean_vectors else np.zeros(20)
    var_mfcc = np.mean(var_vectors, axis=0) if var_vectors else np.zeros(20)

    return np.concatenate([mean_mfcc, var_mfcc])  # Вектор размером 40

async def match_users(user1_id: int, user2_id: int):
    """Сравниваем музыкальные вкусы двух пользователей."""
    vec1 = calculate_mean_mfcc(await get_user_tracks(user1_id))
    vec2 = calculate_mean_mfcc(await get_user_tracks(user2_id))
    return max(0, cosine_similarity([vec1], [vec2])[0][0] * 100)

@router.message(Command("match"))
async def handle_match(message: Message):
    """Обработчик команды /match @username."""
    args = message.text.split()
    if len(args) != 2 or not args[1].startswith("@"):
        return await message.reply("Используй: `/match @username`")

    sender = await get_user_by_chat_id(message.from_user.id)
    receiver = await get_user_by_username(args[1][1:])

    if not sender or not receiver:
        return await message.reply("❌ Пользователь не найден или не зарегистрирован.")

    similarity = await match_users(sender.id, receiver.id)
    await message.reply(f"🎵 Твой музыкальный мэтч с @{receiver.username}: {similarity:.2f}%")

@router.message(CommandStart())
@router.callback_query(F.data == "main_page")
async def start(msg: Message | CallbackQuery):
    """Приветственное сообщение."""
    text = f"Хэллоу, @{msg.from_user.username}! 🌟 Айм - Батя оф Синх"
    buttons = builders.inline_builder(["⚙️ Load song", "🌐 Matching!"], ["song", "match"])

    if isinstance(msg, CallbackQuery):
        await msg.message.edit_text(text, reply_markup=buttons)
        await msg.answer()
    else:
        await msg.answer(text, reply_markup=buttons)

@router.message(F.text)
async def handle_download(msg: Message):
    """Поиск и отправка информации о треке."""
    async with YandexMusicSDK(token=config.access_token.get_secret_value(), upload_dir="downloads") as sdk:
        tracks = await sdk.search_tracks(msg.text, count=1, download=False, lyrics=False)
        if not tracks:
            return await msg.answer("❌ Трек не найден.")

        track = tracks[0]
        await msg.answer(f"🎵 Найдено: {track.title} - {', '.join(track.artists)}",
                         reply_markup=builders.inline_builder(["✔ Confirm"], [f"confirm:{track.title} - {', '.join(track.artists)}"]))
