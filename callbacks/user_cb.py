import os
import aiohttp

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.types.input_file import FSInputFile
import librosa
import numpy as np
from keyboards import builders
from config_reader import config
from yandex import YandexMusicSDK
from handlers import cmds


from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from orm.db import User, Track, AsyncSessionLocal

router = Router()

async def get_or_create_user(username: str, chat_id: int) -> User:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            result = await session.execute(select(User).filter_by(username=username))
            user = result.scalars().first()
            if not user:
                user = User(username=username, chat_id=chat_id)  # <-- Добавили chat_id
                session.add(user)
                await session.commit()
        return user


async def add_track_to_db(user_id: int, title: str, artist: str, mfcc: bytes):
    print(f"Добавляю трек: {title} | {artist} | {len(mfcc)} байт")
    async with AsyncSessionLocal() as session:
        async with session.begin():
            track = Track(user_id=user_id, title=title, artist=artist, mfcc=mfcc)
            session.add(track)
        await session.commit()


@router.callback_query(F.data == 'song')
async def show_song(query: CallbackQuery) -> None:
    await query.answer('Чтобы загрузить песни просто отправляй их название!', reply_markup=builders.inline_builder(['Назад'], ['main_page']))
    await query.answer()

@router.callback_query(F.data == 'match')
async def show_song(query: CallbackQuery) -> None:
    await query.answer('Чтобы узнать свой мэтч с другим /match @username!', reply_markup=builders.inline_builder(['Назад'], ['main_page']))
    await query.answer()

async def extract_mfcc(file_path: str) -> bytes:
    y, sr = librosa.load(file_path, sr=None)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20)
    return np.array(mfcc).tobytes()  # Преобразуем в байты

@router.callback_query(F.data.startswith("confirm:"))
async def delete_song_callback(query: CallbackQuery) -> None:
    name = query.data.split("confirm:")[1]
    async with YandexMusicSDK(token=config.access_token.get_secret_value(), upload_dir="downloads") as sdk:
        tracks = await sdk.search_tracks(name, count=1, download=True, lyrics=False)

        for track in tracks:
            user = await get_or_create_user(query.from_user.username, query.from_user.id)  # <-- Теперь передаем chat_id
            mfcc = await extract_mfcc(track.file_path)  #! Вычисляем MFCC
            await add_track_to_db(user.id, track.title, track.artists[0], mfcc)
            track_count = await cmds.get_user_track_count(user.id)
            await query.message.edit_text(f'✔ {track.title} успешно добавлено в вашу медиатеку! ({track_count})')

@router.callback_query(F.data == "del")
async def del_(query: CallbackQuery):

    await query.answer('Успешно удалено!')
    await query.message.delete()

    await query.answer()