from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from companion.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db():
    async with async_session_maker() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        def _migrate_sqlite_columns(sync_conn):
            # personas
            res_p = sync_conn.exec_driver_sql("PRAGMA table_info(personas)").fetchall()
            p_cols = [r[1] for r in res_p]
            new_p_cols = {
                "district": "VARCHAR(64) DEFAULT 'центр'",
                "dating_app_context": "TEXT DEFAULT 'была в дайвинчике буквально пару дней, вчера удалила анкету потому что надоело, перешла в тг'",
                "study_work_hobbies": "TEXT DEFAULT 'Учусь на 4 курсе графического дизайна, беру заказы на фрилансе. Обожаю выставки, атмосферные кофейни, кофе и прогулки.'",
                "extra_facts": "TEXT DEFAULT 'Люблю атмосферные места, не люблю банальные свидания в душных кафе, давно хотела сходить на крутой квест.'",
                "birth_city": "VARCHAR(64) DEFAULT 'Самара'",
                "move_reason": "TEXT DEFAULT 'Поступила в университет на дизайн и осталась жить'",
                "years_in_city": "VARCHAR(64) DEFAULT 'Живу тут около 4 лет'",
            }
            for col_name, col_type in new_p_cols.items():
                if col_name not in p_cols:
                    sync_conn.exec_driver_sql(f"ALTER TABLE personas ADD COLUMN {col_name} {col_type}")

            # contacts
            res_c = sync_conn.exec_driver_sql("PRAGMA table_info(contacts)").fetchall()
            c_cols = [r[1] for r in res_c]
            if "origin_city" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN origin_city VARCHAR(64) NULL")
            if "client_free_time" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN client_free_time VARCHAR(128) NULL")
            if "language" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN language VARCHAR(16) DEFAULT 'ru'")
            if "funnel_stage" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN funnel_stage VARCHAR(32) DEFAULT 'chatting'")
            if "ai_active" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN ai_active BOOLEAN DEFAULT 1")
            if "alert_date_sent" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN alert_date_sent BOOLEAN DEFAULT 0")
            if "alert_quest_sent" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN alert_quest_sent BOOLEAN DEFAULT 0")
            if "no_brackets" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN no_brackets BOOLEAN DEFAULT 0")
            if "scheduled_reengage_at" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN scheduled_reengage_at TIMESTAMP NULL")
            if "reengage_context" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN reengage_context VARCHAR(64) NULL")
            if "pending_overnight_message" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN pending_overnight_message TEXT NULL")
            if "violation_count" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN violation_count INTEGER DEFAULT 0")
            if "has_violation" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN has_violation BOOLEAN DEFAULT 0")
            if "last_violation_at" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN last_violation_at TIMESTAMP NULL")
            if "last_violation_text" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN last_violation_text TEXT NULL")
            if "last_violation_analysis" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN last_violation_analysis TEXT NULL")
            if "last_voice_file_id" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN last_voice_file_id VARCHAR(256) NULL")
            if "tags" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN tags VARCHAR(256) DEFAULT '🟢 Активный'")
            if "dialogue_summary" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN dialogue_summary TEXT NULL")
            if "dialogue_topics" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN dialogue_topics VARCHAR(256) NULL")

        await conn.run_sync(_migrate_sqlite_columns)
