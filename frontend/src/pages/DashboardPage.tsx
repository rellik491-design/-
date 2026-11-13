import React, { useEffect, useState, useCallback } from "react";
import { apiClient } from "../api/client";
import { DashboardStats, TabKey } from "../types/api";
import { LoadingSkeleton } from "../components/LoadingSkeleton";
import { ErrorBanner } from "../components/ErrorBanner";
import { telegramService } from "../services/telegram";

interface DashboardPageProps {
  onNavigateTab: (tab: TabKey) => void;
}

export const DashboardPage: React.FC<DashboardPageProps> = ({ onNavigateTab }) => {
  const [period, setPeriod] = useState<string>("today");
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const loadStats = useCallback(async (selectedPeriod: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiClient.getDashboardStats(selectedPeriod);
      setStats(res);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить статистику");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadStats(period);
  }, [period, loadStats]);

  const handleExportCsv = () => {
    telegramService.hapticImpact("medium");
    const token = apiClient.getSessionToken();
    const url = `/api/v1/dashboard/export-csv?period=${encodeURIComponent(period)}${
      token ? `&token=${encodeURIComponent(token)}` : ""
    }`;
    window.open(url, "_blank");
  };

  const periods = [
    { code: "today", label: "Сегодня" },
    { code: "yesterday", label: "Вчера" },
    { code: "7d", label: "7 дней" },
    { code: "30d", label: "30 дней" },
    { code: "all", label: "За всё время" },
  ];

  return (
    <div className="page-container">
      <div className="chip-row">
        {periods.map((p) => (
          <button
            key={p.code}
            className={`chip ${period === p.code ? "active" : ""}`}
            onClick={() => {
              telegramService.hapticSelection();
              setPeriod(p.code);
            }}
          >
            {p.label}
          </button>
        ))}
      </div>

      {error && <ErrorBanner message={error} onRetry={() => loadStats(period)} />}

      {loading || !stats ? (
        <LoadingSkeleton count={4} height={85} />
      ) : (
        <>
          <div className="card">
            <div className="card-title">
              <span>📊 Воронка и Конверсия ({stats.period_title})</span>
              <button className="btn btn-secondary" style={{ padding: "5px 10px", fontSize: 11 }} onClick={handleExportCsv}>
                📥 CSV
              </button>
            </div>

            <div className="grid-2" style={{ marginTop: 8 }}>
              <div className="stat-box">
                <div className="stat-value" style={{ color: "#ec4899" }}>
                  🔥 {stats.quest_leads}
                </div>
                <div className="stat-label">Согласились на квест</div>
              </div>
              <div className="stat-box">
                <div className="stat-value" style={{ color: "#f59e0b" }}>
                  ☕ {stats.meetup_leads}
                </div>
                <div className="stat-label">Предложили встречу</div>
              </div>
              <div className="stat-box">
                <div className="stat-value">{stats.total_leads}</div>
                <div className="stat-label">Всего диалогов</div>
              </div>
              <div className="stat-box">
                <div className="stat-value" style={{ color: "#10b981" }}>
                  🟢 {stats.active_leads}
                </div>
                <div className="stat-label">Активно ведет ИИ</div>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-title">💬 Активность и Голосовые</div>
            <div className="grid-3">
              <div className="stat-box">
                <div className="stat-value">{stats.total_messages_in}</div>
                <div className="stat-label">Входящие</div>
              </div>
              <div className="stat-box">
                <div className="stat-value">{stats.total_messages_out}</div>
                <div className="stat-label">Ответы ИИ</div>
              </div>
              <div className="stat-box">
                <div className="stat-value" style={{ color: "#fda4af" }}>
                  🔴 {stats.insult_leads}
                </div>
                <div className="stat-label">Оскорбления</div>
              </div>
            </div>

            <div className="grid-2" style={{ marginTop: 8 }}>
              <div className="stat-box">
                <div className="stat-value">🎙 {stats.incoming_voice_count}</div>
                <div className="stat-label">Распознано ГС (STT)</div>
              </div>
              <div className="stat-box">
                <div className="stat-value">🔊 {stats.outgoing_voice_count}</div>
                <div className="stat-label">Отправлено ГС (TTS)</div>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-title">⚡ Быстрые действия</div>
            <div className="grid-2">
              <button className="btn" onClick={() => onNavigateTab("chats")}>
                💬 Открыть диалоги
              </button>
              <button className="btn btn-secondary" onClick={() => onNavigateTab("persona")}>
                👤 Настроить легенду
              </button>
              <button className="btn btn-secondary" onClick={() => onNavigateTab("accounts")}>
                📱 Telegram Business
              </button>
              <button className="btn btn-secondary" onClick={() => onNavigateTab("voice")}>
                🎙 Лаборатория голоса
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
};
