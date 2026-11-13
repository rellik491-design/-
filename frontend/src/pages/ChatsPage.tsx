import React, { useEffect, useState, useCallback } from "react";
import { apiClient } from "../api/client";
import {
  ContactItem,
  ContactMessageItem,
  ConversationContextInspect,
} from "../types/api";
import { LoadingSkeleton } from "../components/LoadingSkeleton";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { useTelegramBackButton } from "../hooks/useTelegramBackButton";
import { telegramService } from "../services/telegram";

interface ChatsPageProps {
  selectedContactId: number | null;
  onSelectContact: (id: number | null) => void;
}

export const ChatsPage: React.FC<ChatsPageProps> = ({
  selectedContactId,
  onSelectContact,
}) => {
  const [filter, setFilter] = useState<string>("all");
  const [search, setSearch] = useState<string>("");
  const [contacts, setContacts] = useState<ContactItem[]>([]);
  const [loadingList, setLoadingList] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Detail state
  const [activeContact, setActiveContact] = useState<ContactItem | null>(null);
  const [messages, setMessages] = useState<ContactMessageItem[]>([]);
  const [contextData, setContextData] = useState<ConversationContextInspect | null>(null);
  const [detailTab, setDetailTab] = useState<"messages" | "memory">("messages");
  const [loadingDetail, setLoadingDetail] = useState<boolean>(false);
  const [messageInput, setMessageInput] = useState<string>("");
  const [sendMode, setSendMode] = useState<"owner_manual" | "simulate_incoming">("owner_manual");
  const [sending, setSending] = useState<boolean>(false);

  useTelegramBackButton(Boolean(selectedContactId), () => onSelectContact(null));

  const fetchContacts = useCallback(async () => {
    setLoadingList(true);
    setError(null);
    try {
      const list = await apiClient.listContacts(filter, search);
      setContacts(list);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Ошибка загрузки списка лидов");
    } finally {
      setLoadingList(false);
    }
  }, [filter, search]);

  useEffect(() => {
    fetchContacts();
  }, [fetchContacts]);

  const loadContactDetail = useCallback(async (contactId: number) => {
    setLoadingDetail(true);
    setError(null);
    try {
      const [msgs, ctx, all] = await Promise.all([
        apiClient.getContactMessages(contactId),
        apiClient.getContactContext(contactId),
        apiClient.listContacts("all"),
      ]);
      const found = all.find((c) => c.id === contactId) || null;
      setActiveContact(found);
      setMessages(msgs);
      setContextData(ctx);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить диалог");
    } finally {
      setLoadingDetail(false);
    }
  }, []);

  useEffect(() => {
    if (selectedContactId !== null) {
      loadContactDetail(selectedContactId);
    } else {
      setActiveContact(null);
    }
  }, [selectedContactId, loadContactDetail]);

  const handleTogglePause = async () => {
    if (!activeContact) return;
    telegramService.hapticImpact("medium");
    try {
      const updated = activeContact.bot_paused
        ? await apiClient.resumeContact(activeContact.id)
        : await apiClient.pauseContact(activeContact.id);
      setActiveContact(updated);
      fetchContacts();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Ошибка изменения статуса ИИ");
    }
  };

  const handleBlock = async () => {
    if (!activeContact) return;
    telegramService.hapticNotification("warning");
    try {
      const updated = await apiClient.blockContact(activeContact.id);
      setActiveContact(updated);
      fetchContacts();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Ошибка блокировки контакта");
    }
  };

  const handleDelete = async () => {
    if (!activeContact) return;
    if (!window.confirm("Удалить контакт и всю историю переписки?")) return;
    telegramService.hapticNotification("warning");
    try {
      await apiClient.deleteContact(activeContact.id);
      onSelectContact(null);
      fetchContacts();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Ошибка удаления контакта");
    }
  };

  const handleToggleTag = async (tagCode: string) => {
    if (!activeContact) return;
    telegramService.hapticSelection();
    try {
      const updated = await apiClient.toggleContactTag(activeContact.id, tagCode);
      setActiveContact(updated);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Не удалось обновить тег");
    }
  };

  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeContact || !messageInput.trim() || sending) return;
    const text = messageInput.trim();
    setSending(true);
    setMessageInput("");
    telegramService.hapticImpact("light");
    try {
      const res = await apiClient.sendOrSimulateMessage(activeContact.id, text, sendMode);
      setActiveContact(res.contact);
      await loadContactDetail(activeContact.id);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Не удалось отправить сообщение");
    } finally {
      setSending(false);
    }
  };

  if (selectedContactId !== null) {
    return (
      <div className="page-container">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
          <button className="btn btn-secondary" onClick={() => onSelectContact(null)}>
            ◀️ Назад к списку
          </button>
          <div style={{ display: "flex", gap: 6 }}>
            <button
              className={`chip ${detailTab === "messages" ? "active" : ""}`}
              onClick={() => setDetailTab("messages")}
            >
              💬 Переписка ({messages.length})
            </button>
            <button
              className={`chip ${detailTab === "memory" ? "active" : ""}`}
              onClick={() => setDetailTab("memory")}
            >
              🧠 Контекст & Память ({contextData?.memories.length || 0})
            </button>
          </div>
        </div>

        {error && <ErrorBanner message={error} onRetry={() => loadContactDetail(selectedContactId)} />}

        {loadingDetail || !activeContact ? (
          <LoadingSkeleton count={3} height={90} />
        ) : (
          <>
            <div className="card">
              <div className="card-title">
                <div>
                  <span>{activeContact.first_name || "Собеседник"}</span>{" "}
                  {activeContact.username && (
                    <a
                      href={`https://t.me/${activeContact.username}`}
                      target="_blank"
                      rel="noreferrer"
                      style={{ color: "var(--link-color)", fontSize: 12 }}
                    >
                      @{activeContact.username}
                    </a>
                  )}
                </div>
                <span className="badge">
                  {activeContact.bot_paused ? "⏸ На паузе" : "🟢 ИИ активен"}
                </span>
              </div>

              <div style={{ fontSize: 12, color: "var(--hint-color)", display: "flex", flexWrap: "wrap", gap: 8 }}>
                <span>📍 Город: <b>{activeContact.detected_city || "Не определен"}</b></span>
                <span>🎯 Этап: <b>{activeContact.funnel_stage}</b></span>
                <span>📨 Сообщений: <b>{activeContact.total_messages_in} вх / {activeContact.total_messages_out} исх</b></span>
              </div>

              {activeContact.tags && (
                <div style={{ marginTop: 8, fontSize: 12 }}>
                  🏷 <b>Теги:</b> {activeContact.tags}
                </div>
              )}

              {activeContact.has_violation && (
                <div
                  style={{
                    marginTop: 8,
                    padding: 8,
                    borderRadius: 8,
                    background: "rgba(244, 63, 94, 0.14)",
                    border: "1px solid rgba(244, 63, 94, 0.35)",
                    fontSize: 12,
                  }}
                >
                  🔴 <b>Зафиксирована грубость ({activeContact.violation_count}):</b>{" "}
                  {activeContact.last_violation_analysis || activeContact.last_violation_text}
                </div>
              )}

              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 10 }}>
                <button
                  className={`btn ${activeContact.bot_paused ? "btn-success" : "btn-secondary"}`}
                  onClick={handleTogglePause}
                >
                  {activeContact.bot_paused ? "▶️ Включить ИИ" : "⏸ Пауза ИИ (Перехват)"}
                </button>
                <button className="btn btn-secondary" onClick={() => handleToggleTag("vip")}>
                  ⭐️ VIP
                </button>
                <button className="btn btn-danger" onClick={handleBlock}>
                  ⛔️ В блок
                </button>
                <button className="btn btn-danger" onClick={handleDelete}>
                  🗑 Удалить
                </button>
              </div>
            </div>

            {detailTab === "messages" ? (
              <div className="card" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <div
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: 8,
                    maxHeight: 340,
                    overflowY: "auto",
                    paddingRight: 4,
                  }}
                >
                  {messages.length === 0 ? (
                    <div style={{ color: "var(--hint-color)", textAlign: "center", padding: 20 }}>
                      Сообщений пока нет
                    </div>
                  ) : (
                    messages.map((m) => (
                      <div
                        key={m.id}
                        className={`chat-bubble ${m.sender_role === "user" ? "user" : "bot"}`}
                        style={{ opacity: m.is_deleted ? 0.45 : 1 }}
                      >
                        <div style={{ fontSize: 10, opacity: 0.75, marginBottom: 2 }}>
                          {m.sender_role === "user" ? "👨 Мужчина" : "👩 Девушка (ИИ / Вы)"}{" "}
                          {m.is_voice && `🎙 ГС (${m.voice_duration}с)`}{" "}
                          {m.is_edited && "✏️ изм."}{" "}
                          {m.is_deleted && "🗑 удалено"}
                        </div>
                        <div>{m.text}</div>
                      </div>
                    ))
                  )}
                </div>

                <form onSubmit={handleSendMessage} style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 6 }}>
                  <div style={{ display: "flex", gap: 6 }}>
                    <button
                      type="button"
                      className={`chip ${sendMode === "owner_manual" ? "active" : ""}`}
                      onClick={() => setSendMode("owner_manual")}
                    >
                      ✍️ Ответить самой (пауза ИИ 15м)
                    </button>
                    <button
                      type="button"
                      className={`chip ${sendMode === "simulate_incoming" ? "active" : ""}`}
                      onClick={() => setSendMode("simulate_incoming")}
                    >
                      🧪 Тест реплики от парня
                    </button>
                  </div>
                  <div style={{ display: "flex", gap: 8 }}>
                    <input
                      className="input"
                      placeholder={
                        sendMode === "owner_manual"
                          ? "Написать сообщение в чат от своего имени..."
                          : "Введите реплику мужчины для проверки ответа ИИ..."
                      }
                      value={messageInput}
                      onChange={(e) => setMessageInput(e.target.value)}
                    />
                    <button className="btn" type="submit" disabled={sending || !messageInput.trim()}>
                      {sending ? "..." : "➤"}
                    </button>
                  </div>
                </form>
              </div>
            ) : (
              <div className="card" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <div className="card-title">🧠 7-уровневый Контекст и Долгосрочная Память ИИ</div>
                <div className="stat-box">
                  <div style={{ fontWeight: 700, fontSize: 12 }}>Краткая сводка (Level 3 Summary):</div>
                  <div style={{ fontSize: 12, marginTop: 4 }}>
                    {contextData?.summary_text || activeContact.dialogue_summary || "Формируется по мере общения..."}
                  </div>
                </div>
                <div className="grid-2">
                  <div className="stat-box">
                    <div style={{ fontWeight: 700, fontSize: 12 }}>Активные темы:</div>
                    <div style={{ fontSize: 12, marginTop: 4 }}>
                      {contextData?.active_topics || activeContact.dialogue_topics || "Знакомство"}
                    </div>
                  </div>
                  <div className="stat-box">
                    <div style={{ fontWeight: 700, fontSize: 12 }}>Договоренности:</div>
                    <div style={{ fontSize: 12, marginTop: 4 }}>
                      {contextData?.commitments || "Пока нет"}
                    </div>
                  </div>
                </div>

                <div style={{ fontWeight: 700, fontSize: 13, marginTop: 4 }}>
                  📌 Сохраненные факты о собеседнике (Long-Term Memory):
                </div>
                {contextData?.memories && contextData.memories.length > 0 ? (
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    {contextData.memories.map((mem) => (
                      <div key={mem.id} className="stat-box" style={{ display: "flex", justifyContent: "space-between" }}>
                        <div>
                          <span className="badge" style={{ marginRight: 6 }}>{mem.category}</span>
                          <b>{mem.key}:</b> {mem.value}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div style={{ fontSize: 12, color: "var(--hint-color)" }}>
                    Долгосрочные факты (работа, город, авто, хобби, предпочтения) автоматически извлекаются из диалога без мусора.
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    );
  }

  const filters = [
    { code: "all", label: "Все" },
    { code: "hot", label: "🔥 Встречи / Квест" },
    { code: "active", label: "🟢 Активные" },
    { code: "paused", label: "⏸ На паузе" },
    { code: "insults", label: "🔴 Оскорбления" },
    { code: "blocked", label: "⛔️ Блок" },
  ];

  return (
    <div className="page-container">
      <input
        className="input"
        placeholder="🔍 Поиск по имени, @username, городу или ID..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />

      <div className="chip-row">
        {filters.map((f) => (
          <button
            key={f.code}
            className={`chip ${filter === f.code ? "active" : ""}`}
            onClick={() => {
              telegramService.hapticSelection();
              setFilter(f.code);
            }}
          >
            {f.label}
          </button>
        ))}
      </div>

      {error && <ErrorBanner message={error} onRetry={fetchContacts} />}

      {loadingList ? (
        <LoadingSkeleton count={4} height={76} />
      ) : contacts.length === 0 ? (
        <EmptyState
          icon="💬"
          title="Диалоги не найдены"
          description="Как только мужчины начнут писать в подключенный Telegram-аккаунт, диалоги появятся здесь в реальном времени."
        />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {contacts.map((c) => (
            <div
              key={c.id}
              className="card"
              style={{ cursor: "pointer", padding: "12px 14px" }}
              onClick={() => {
                telegramService.hapticSelection();
                onSelectContact(c.id);
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ fontWeight: 700, fontSize: 14 }}>
                  {c.first_name || "Собеседник"}{" "}
                  {c.username && <span style={{ color: "var(--hint-color)", fontWeight: 400 }}>@{c.username}</span>}
                </div>
                <span className="badge">
                  {c.funnel_stage === "quest_agreed"
                    ? "🎉 Квест!"
                    : c.funnel_stage === "date_proposed"
                    ? "🔥 Свидание"
                    : c.bot_paused
                    ? "⏸ Пауза"
                    : "🟢 ИИ"}
                </span>
              </div>
              <div style={{ fontSize: 12, color: "var(--hint-color)", marginTop: 4 }}>
                {c.dialogue_summary || "Нажмите для просмотра истории и памяти ИИ"}
              </div>
              <div style={{ display: "flex", gap: 10, marginTop: 6, fontSize: 11, color: "var(--hint-color)" }}>
                <span>📍 {c.detected_city || "Город не указан"}</span>
                <span>💬 {c.total_messages_in + c.total_messages_out} сообщ.</span>
                {c.has_violation && <span style={{ color: "#fda4af" }}>🔴 Жалоба ({c.violation_count})</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
