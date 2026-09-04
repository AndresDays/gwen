from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gwen.models import ConversationState, Memory, Message, UsageDay


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

    async def clear_messages(self, user_id: int) -> int:
        result = await self.session.execute(delete(Message).where(Message.user_id == user_id))
        await self.session.commit()
        await self.session.execute(
            delete(ConversationState).where(ConversationState.user_id == user_id)
        )
        await self.session.commit()
        return result.rowcount or 0  # type: ignore[attr-defined]

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

    async def conversation_summary(self, user_id: int) -> str:
        state = await self.session.get(ConversationState, user_id)
        return state.summary if state else ""

    async def compact_messages(self, user_id: int, keep: int, summary_limit: int = 6000) -> bool:
        query = select(Message).where(Message.user_id == user_id).order_by(Message.id)
        messages = list((await self.session.scalars(query)).all())
        if len(messages) <= keep * 2:
            return False
        archived = messages[:-keep]
        previous = await self.conversation_summary(user_id)
        transcript = "\n".join(f"{item.role}: {item.content}" for item in archived)
        summary = (previous + "\n" + transcript).strip()[-summary_limit:]
        state = await self.session.get(ConversationState, user_id)
        if state:
            state.summary = summary
        else:
            self.session.add(ConversationState(user_id=user_id, summary=summary))
        await self.session.execute(
            delete(Message).where(Message.id.in_([item.id for item in archived]))
        )
        await self.session.commit()
        return True

    async def usage_tokens(self, user_id: int, day: date) -> int:
        query = select(
            func.coalesce(func.sum(UsageDay.input_tokens + UsageDay.output_tokens), 0)
        ).where(UsageDay.user_id == user_id, UsageDay.day == day)
        return int(await self.session.scalar(query) or 0)

    async def add_usage(
        self,
        user_id: int,
        day: date,
        input_tokens: int = 0,
        output_tokens: int = 0,
        voice_seconds: int = 0,
        tts_characters: int = 0,
    ) -> None:
        query = select(UsageDay).where(UsageDay.user_id == user_id, UsageDay.day == day)
        usage = await self.session.scalar(query)
        if usage:
            usage.input_tokens += input_tokens
            usage.output_tokens += output_tokens
            usage.voice_seconds += voice_seconds
            usage.tts_characters += tts_characters
        else:
            self.session.add(
                UsageDay(
                    user_id=user_id,
                    day=day,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    voice_seconds=voice_seconds,
                    tts_characters=tts_characters,
                )
            )
        await self.session.commit()

    async def voice_usage(self, user_id: int, day: date) -> tuple[int, int]:
        query = select(
            func.coalesce(func.sum(UsageDay.voice_seconds), 0),
            func.coalesce(func.sum(UsageDay.tts_characters), 0),
        ).where(UsageDay.user_id == user_id, UsageDay.day == day)
        row = (await self.session.execute(query)).one()
        return int(row[0]), int(row[1])
