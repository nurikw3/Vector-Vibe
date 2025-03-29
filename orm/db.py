from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy import Column, Integer, LargeBinary, String, ForeignKey
from config_reader import config
from sqlalchemy.future import select


DATABASE_URL = config.DATABASE_URL_asyncpg

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False)
    chat_id = Column(Integer, unique=True, nullable=False)

    tracks = relationship("Track", back_populates="user", cascade="all, delete-orphan")

class Track(Base):
    __tablename__ = "tracks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String, nullable=False)
    artist = Column(String, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    mfcc = Column(LargeBinary, nullable=True)  # Новый столбец

    user = relationship("User", back_populates="tracks")

engine = create_async_engine(DATABASE_URL, echo=True)
AsyncSessionLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def reset_database():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)  # Удаляет все таблицы
        await conn.run_sync(Base.metadata.create_all)  # Создает заново

async def add_user(username: str, chat_id: int):
    async with AsyncSessionLocal() as session:
        async with session.begin():
            user = User(username=username, chat_id=chat_id)
            session.add(user)
        await session.commit()
        return user

async def add_track(user_id: int, title: str, artist: str):
    async with AsyncSessionLocal() as session:
        async with session.begin():
            track = Track(user_id=user_id, title=title, artist=artist)
            session.add(track)
        await session.commit()
        return track

async def get_user_tracks(user_id: int):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Track).filter_by(user_id=user_id))
        return result.scalars().all()
