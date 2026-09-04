from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from gwen.models import Memory, Message


class Repository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_message(self, user_id: int, role: str, content: str) -> None:
        self.session.add(Message(user_id=user_id, role=role, content=content))
        await self.session.commit()

    async def recent_messages(self, user_id: int, limit: int) -> list[Message]:
        query = (
            select(Message)
            .where(Message.user_id == user_id)
            .order_by(Message.id.desc())
            .limit(limit)
        )
        rows = list((await self.session.scalars(query)).all())
        return list(reversed(rows))

    async def add_memory(self, user_id: int, content: str) -> Memory:
        memory = Memory(user_id=user_id, content=content.strip())
        self.session.add(memory)
        await self.session.commit()
        await self.session.refresh(memory)
        return memory

    async def memories(self, user_id: int) -> list[Memory]:
        query = select(Memory).where(Memory.user_id == user_id).order_by(Memory.id)
        return list((await self.session.scalars(query)).all())

    async def forget_matching(self, user_id: int, text: str) -> int:
        query = delete(Memory).where(
            Memory.user_id == user_id,
            Memory.content.ilike(f"%{text.strip()}%"),
        )
        result = await self.session.execute(query)
        await self.session.commit()
        return result.rowcount or 0  # type: ignore[attr-defined]
