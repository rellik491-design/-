import asyncio
import sys
sys.path.insert(0, "src")
from companion.models import init_db, async_session_maker, Contact, AccountSession
from sqlalchemy import select

async def main():
    await init_db()
    async with async_session_maker() as s:
        res_c = await s.execute(select(Contact))
        contacts = res_c.scalars().all()
        print("Contacts:", len(contacts))
        for c in contacts:
            print(f"ID={c.id}, tg_id={c.tg_id}, name={c.first_name}, in={c.total_messages_in}, out={c.total_messages_out}, stage={c.funnel_stage}")

        res_a = await s.execute(select(AccountSession))
        accounts = res_a.scalars().all()
        print("\nAccounts:", len(accounts))
        for a in accounts:
            print(f"ID={a.id}, conn_id={a.business_connection_id}, tg_user_id={a.tg_user_id}, name={a.first_name}")

if __name__ == "__main__":
    asyncio.run(main())
