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
                "country": "VARCHAR(64) DEFAULT 'Россия'",
                "country_code": "VARCHAR(8) DEFAULT 'RU'",
                "sleep_time": "VARCHAR(5) DEFAULT '23:00'",
                "wake_time": "VARCHAR(5) DEFAULT '09:00'",
                "work_start": "VARCHAR(5) DEFAULT '10:00'",
                "work_end": "VARCHAR(5) DEFAULT '18:00'",
                "trip_city": "VARCHAR(64) NULL",
                "trip_country": "VARCHAR(64) NULL",
                "trip_country_code": "VARCHAR(8) NULL",
                "trip_timezone": "VARCHAR(64) NULL",
                "location_extra": "TEXT NULL",
                "city_connections_data": "TEXT DEFAULT '[]'",
                "activity_catalog_data": "TEXT DEFAULT '[]'",
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
            if "quest_alarm_muted" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN quest_alarm_muted BOOLEAN DEFAULT 0")
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
            if "preferred_name" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN preferred_name VARCHAR(64) NULL")
            if "communication_style" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN communication_style VARCHAR(32) DEFAULT 'casual'")
            if "preferred_response_length" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN preferred_response_length VARCHAR(32) DEFAULT 'medium'")
            if "emoji_frequency" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN emoji_frequency VARCHAR(32) DEFAULT 'medium'")
            if "likes_multi_message_responses" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN likes_multi_message_responses BOOLEAN DEFAULT 1")
            if "discovered_profile_data" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN discovered_profile_data TEXT NULL")
            if "dating_discovery_source" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN dating_discovery_source VARCHAR(64) NULL")
            if "language_preference" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN language_preference TEXT NULL")
            if "recent_language" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN recent_language VARCHAR(16) NULL")
            if "language_pattern" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN language_pattern VARCHAR(16) NULL")
            if "contact_deflection_count" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN contact_deflection_count INTEGER DEFAULT 0")
            if "last_contact_request_at" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN last_contact_request_at TIMESTAMP NULL")
            if "origin_country" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN origin_country VARCHAR(64) NULL")
            if "current_country" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN current_country VARCHAR(64) NULL")
            if "current_city" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN current_city VARCHAR(64) NULL")
            if "home_country" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN home_country VARCHAR(64) NULL")
            if "home_city" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN home_city VARCHAR(64) NULL")
            if "family_location" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN family_location VARCHAR(64) NULL")
            if "temporary_location" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN temporary_location VARCHAR(64) NULL")
            if "geo_profile_data" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN geo_profile_data TEXT NULL")
            if "persona_lore_state" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN persona_lore_state TEXT NULL")
            if "meeting_state_data" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN meeting_state_data TEXT NULL")
            if "active_thread" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN active_thread VARCHAR(64) DEFAULT 'general'")
            if "correction_events" not in c_cols:
                sync_conn.exec_driver_sql("ALTER TABLE contacts ADD COLUMN correction_events TEXT NULL")

            # Automatic status migration for quest/date leads
            sync_conn.exec_driver_sql("""
                UPDATE contacts 
                SET status = 'LEAD_MEETING' 
                WHERE (funnel_stage IN ('quest_agreed', 'date_proposed') OR alert_quest_sent = 1 OR alert_date_sent = 1 OR status = 'lead_meeting')
            """)

            # processed_messages table (Sections 10-13: Idempotency & Message Ownership)
            sync_conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS processed_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                sender_id INTEGER NULL,
                sender_type VARCHAR(16) DEFAULT 'USER',
                outgoing BOOLEAN DEFAULT 0,
                status VARCHAR(32) DEFAULT 'PROCESSED',
                received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                processed_at TIMESTAMP NULL,
                CONSTRAINT uq_chat_message UNIQUE (chat_id, message_id)
            )
            """)
            sync_conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_proc_msg_chat_mid ON processed_messages(chat_id, message_id)")

            # contact_messages
            res_cm = sync_conn.exec_driver_sql("PRAGMA table_info(contact_messages)").fetchall()
            cm_cols = [r[1] for r in res_cm]
            if cm_cols:
                if "telegram_message_id" not in cm_cols:
                    sync_conn.exec_driver_sql("ALTER TABLE contact_messages ADD COLUMN telegram_message_id INTEGER NULL")
                if "reply_to_msg_id" not in cm_cols:
                    sync_conn.exec_driver_sql("ALTER TABLE contact_messages ADD COLUMN reply_to_msg_id INTEGER NULL")
                if "media_type" not in cm_cols:
                    sync_conn.exec_driver_sql("ALTER TABLE contact_messages ADD COLUMN media_type VARCHAR(24) DEFAULT 'text'")
                if "processing_status" not in cm_cols:
                    sync_conn.exec_driver_sql("ALTER TABLE contact_messages ADD COLUMN processing_status VARCHAR(24) DEFAULT 'PROCESSED'")
                if "batch_id" not in cm_cols:
                    sync_conn.exec_driver_sql("ALTER TABLE contact_messages ADD COLUMN batch_id VARCHAR(64) NULL")
                if "is_edited" not in cm_cols:
                    sync_conn.exec_driver_sql("ALTER TABLE contact_messages ADD COLUMN is_edited BOOLEAN DEFAULT 0")
                if "is_deleted" not in cm_cols:
                    sync_conn.exec_driver_sql("ALTER TABLE contact_messages ADD COLUMN is_deleted BOOLEAN DEFAULT 0")
                if "detected_language" not in cm_cols:
                    sync_conn.exec_driver_sql("ALTER TABLE contact_messages ADD COLUMN detected_language VARCHAR(16) NULL")
                if "language_confidence" not in cm_cols:
                    sync_conn.exec_driver_sql("ALTER TABLE contact_messages ADD COLUMN language_confidence FLOAT NULL")
                if "stt_confidence" not in cm_cols:
                    sync_conn.exec_driver_sql("ALTER TABLE contact_messages ADD COLUMN stt_confidence FLOAT NULL")
                if "normalized_text" not in cm_cols:
                    sync_conn.exec_driver_sql("ALTER TABLE contact_messages ADD COLUMN normalized_text TEXT NULL")

        await conn.run_sync(_migrate_sqlite_columns)
