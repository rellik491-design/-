import React, { useEffect, useState, useCallback } from "react";
import { apiClient } from "../api/client";
import { AccountItem, InviteLinkResponse } from "../types/api";
import { LoadingSkeleton } from "../components/LoadingSkeleton";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { telegramService } from "../services/telegram";

interface AccountsPageProps {
  botUsername: string;
}

export const AccountsPage: React.FC<AccountsPageProps> = ({ botUsername }) => {
  const [accounts, setAccounts] = useState<AccountItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [createdInvite, setCreatedInvite] = useState<InviteLinkResponse | null>(null);
  const [creatingPlan, setCreatingPlan] = useState<string | null>(null);

  const loadAccounts = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await apiClient.listAccounts();
      setAccounts(list);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить аккаунты");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAccounts();
  }, [loadAccounts]);

  const handleToggleAccount = async (id: number) => {
    telegramService.hapticImpact("medium");
    try {
      await apiClient.toggleAccount(id);
      await loadAccounts();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Ошибка переключения аккаунта");
    }
  };

  const handleDeleteAccount = async (id: number) => {
    if (!window.confirm("Отключить и удалить этот аккаунт из системы?")) return;
    telegramService.hapticNotification("warning");
    try {
      await apiClient.deleteAccount(id);
      await loadAccounts();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Ошибка удаления аккаунта");
    }
  };

  const handleCreateInvite = async (planCode: string) => {
    setCreatingPlan(planCode);
    setError(null);
    telegramService.hapticImpact("light");
    try {
      const res = await apiClient.createInviteLink(planCode);
      setCreatedInvite(res);
      telegramService.hapticNotification("success");
    } catch (err: unknown) {
      telegramService.hapticNotification("error");
      setError(err instanceof Error ? err.message : "Не удалось создать ссылку");
    } finally {
      setCreatingPlan(null);
    }
  };

  const copyInviteLink = () => {
    if (!createdInvite) return;
    navigator.clipboard.writeText(createdInvite.deep_link);
    telegramService.hapticNotification("success");
  };

  return (
    <div className="page-container">
      {error && <ErrorBanner message={error} onRetry={loadAccounts} />}

      <div className="card">
        <div className="card-title">🔗 Подключение через Telegram Business</div>
        <div style={{ fontSize: 12, color: "var(--hint-color)", lineHeight: 1.5 }}>
          1. Откройте <b>Настройки Telegram → Telegram для бизнеса → Чат-боты</b>.<br />
          2. Укажите юзернейм бота: <code>@{botUsername}</code> и включите разрешение <b>«Отвечать на сообщения»</b>.<br />
          3. Или сгенерируйте персональную ссылку активации ниже:
        </div>

        <div className="grid-2" style={{ marginTop: 10 }}>
          {["3d", "7d", "14d", "31d"].map((code) => (
            <button
              key={code}
              className="btn btn-secondary"
              disabled={creatingPlan !== null}
              onClick={() => handleCreateInvite(code)}
            >
              {creatingPlan === code ? "⏳..." : `🔗 Ссылка на ${code.replace("d", " дн.")}`}
            </button>
          ))}
        </div>

        {createdInvite && (
          <div
            className="stat-box"
            style={{
              marginTop: 10,
              borderColor: "rgba(16, 185, 129, 0.4)",
              background: "rgba(16, 185, 129, 0.1)",
            }}
          >
            <div style={{ fontWeight: 700, fontSize: 12, color: "#6ee7b7" }}>
              ✅ Персональная ссылка создана ({createdInvite.plan}):
            </div>
            <div
              style={{
                fontSize: 12,
                wordBreak: "break-all",
                margin: "6px 0",
                fontFamily: "monospace",
              }}
            >
              {createdInvite.deep_link}
            </div>
            <button className="btn" style={{ width: "100%" }} onClick={copyInviteLink}>
              📋 Скопировать ссылку
            </button>
          </div>
        )}
      </div>

      <div className="card-title" style={{ padding: "0 4px" }}>
        <span>📱 Подключенные аккаунты ({accounts.length})</span>
      </div>

      {loading ? (
        <LoadingSkeleton count={2} height={85} />
      ) : accounts.length === 0 ? (
        <EmptyState
          icon="📱"
          title="Нет подключенных аккаунтов"
          description={`Добавьте @${botUsername} в разделе «Telegram для бизнеса → Чат-боты», и аккаунт появится здесь автоматически.`}
        />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {accounts.map((acc) => (
            <div key={acc.id} className="card">
              <div className="card-title">
                <div>
                  <span>{acc.first_name || "Аккаунт"}</span>{" "}
                  {acc.username && (
                    <span style={{ color: "var(--hint-color)", fontWeight: 400 }}>
                      @{acc.username}
                    </span>
                  )}
                </div>
                <span className="badge">
                  {acc.is_active ? "🟢 Активен" : "⏸ Приостановлен"}
                </span>
              </div>

              <div style={{ fontSize: 12, color: "var(--hint-color)", display: "flex", flexWrap: "wrap", gap: 10 }}>
                <span>👩 Анкета: <b>{acc.persona_name || "Алина"}</b> ({acc.persona_city || "Москва"})</span>
                <span>🎭 Стиль: <b>{acc.persona_style || "adaptive_all"}</b></span>
              </div>

              <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                <button
                  className={`btn ${acc.is_active ? "btn-secondary" : "btn-success"}`}
                  style={{ flex: 1 }}
                  onClick={() => handleToggleAccount(acc.id)}
                >
                  {acc.is_active ? "⏸ Поставить на паузу" : "▶️ Включить автоответ"}
                </button>
                <button className="btn btn-danger" onClick={() => handleDeleteAccount(acc.id)}>
                  🗑 Отключить
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
