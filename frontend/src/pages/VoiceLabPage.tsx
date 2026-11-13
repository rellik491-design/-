import React, { useState } from "react";
import { apiClient } from "../api/client";
import { ErrorBanner } from "../components/ErrorBanner";
import { telegramService } from "../services/telegram";

export const VoiceLabPage: React.FC = () => {
  const [style, setStyle] = useState<string>("adaptive_all");
  const [customText, setCustomText] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(false);
  const [result, setResult] = useState<{
    raw_text: string;
    prepared_phonetics: string;
    sent_to_telegram: boolean;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const styles = [
    { code: "adaptive_all", label: "🎭 Адаптивная (Универсал)" },
    { code: "playful", label: "🔥 Игривая (Кокетка)" },
    { code: "shy", label: "🌸 Скромная (Нежная)" },
    { code: "sarcastic", label: "⚡ Дерзкая (Сарказм)" },
    { code: "caring", label: "☕ Заботливая (Уют)" },
  ];

  const handleTestVoice = async (sendToTg: boolean) => {
    setLoading(true);
    setError(null);
    telegramService.hapticImpact("medium");
    try {
      const res = await apiClient.testVoice(style, customText.trim() || undefined, sendToTg);
      setResult(res);
      telegramService.hapticNotification("success");
    } catch (err: unknown) {
      telegramService.hapticNotification("error");
      setError(err instanceof Error ? err.message : "Ошибка синтеза голосового сообщения");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="page-container">
      {error && <ErrorBanner message={error} />}

      <div className="card">
        <div className="card-title">🎙 Лаборатория Голоса (TTS + Фонетика)</div>
        <div style={{ fontSize: 12, color: "var(--hint-color)", marginBottom: 10 }}>
          Проверка живой русской речи с естественными вдохами, паузами, ударениями через «ё» и фильтрацией телефонного микрофона (OGG Opus).
        </div>

        <label style={{ fontSize: 11, color: "var(--hint-color)" }}>Выберите интонацию / характер:</label>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
          {styles.map((s) => (
            <button
              key={s.code}
              type="button"
              className={`btn ${style === s.code ? "" : "btn-secondary"}`}
              style={{ justifyContent: "flex-start" }}
              onClick={() => {
                telegramService.hapticSelection();
                setStyle(s.code);
              }}
            >
              {s.label}
            </button>
          ))}
        </div>

        <div style={{ marginTop: 12 }}>
          <label style={{ fontSize: 11, color: "var(--hint-color)" }}>
            Свой текст для озвучки (необязательно):
          </label>
          <textarea
            className="textarea"
            rows={3}
            placeholder="Оставьте пустым для эталонной фразы выбранного стиля или введите свой текст..."
            value={customText}
            onChange={(e) => setCustomText(e.target.value)}
          />
        </div>

        <div className="grid-2" style={{ marginTop: 10 }}>
          <button
            className="btn"
            disabled={loading}
            onClick={() => handleTestVoice(true)}
          >
            {loading ? "⏳ Синтез..." : "🔊 Отправить ГС в чат"}
          </button>
          <button
            className="btn btn-secondary"
            disabled={loading}
            onClick={() => handleTestVoice(false)}
          >
            🔍 Проверить фонетику
          </button>
        </div>
      </div>

      {result && (
        <div className="card">
          <div className="card-title">
            <span>✅ Результат подготовки речи</span>
            {result.sent_to_telegram && <span className="badge">Отправлено в Telegram</span>}
          </div>
          <div className="stat-box" style={{ marginBottom: 8 }}>
            <div style={{ fontSize: 11, color: "var(--hint-color)" }}>Исходный текст:</div>
            <div style={{ fontSize: 13, marginTop: 2 }}>{result.raw_text}</div>
          </div>
          <div className="stat-box">
            <div style={{ fontSize: 11, color: "var(--hint-color)" }}>
              Фонетическая разметка (дыхание, омографы, паузы):
            </div>
            <div style={{ fontSize: 13, marginTop: 2, fontFamily: "monospace" }}>
              {result.prepared_phonetics}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
