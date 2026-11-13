import React, { useEffect, useState, useCallback } from "react";
import { apiClient } from "../api/client";
import { AdminUserOverview } from "../types/api";
import { LoadingSkeleton } from "../components/LoadingSkeleton";
import { ErrorBanner } from "../components/ErrorBanner";
import { telegramService } from "../services/telegram";

export const AdminPage: React.FC = () => {
  const [users, setUsers] = useState<AdminUserOverview[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [target, setTarget] = useState<string>("");
  const [daysOrPlan, setDaysOrPlan] = useState<string>("14d");
  const [granting, setGranting] = useState<boolean>(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadUsers = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await apiClient.adminListUsers();
      setUsers(list);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Ошибка загрузки списка пользователей");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadUsers();
  }, [loadUsers]);

  const handleGrant = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!target.trim() || granting) return;
    setGranting(true);
    setError(null);
    setStatusMsg(null);
    try {
      const res = await apiClient.adminGrantSubscription(target.trim(), daysOrPlan);
      telegramService.hapticNotification("success");
      setStatusMsg(`✅ Выдана подписка на ${res.days_granted} дн. пользователю ID ${res.telegram_id}`);
      setTarget("");
      await loadUsers();
    } catch (err: unknown) {
      telegramService.hapticNotification("error");
      setError(err instanceof Error ? err.message : "Не удалось выдать подписку");
    } finally {
      setGranting(false);
    }
  };

  const handleCancelUserSub = async (userId: number) => {
    if (!window.confirm("Аннулировать подписку этого пользователя?")) return;
    try {
      await apiClient.adminCancelUserSubscription(userId);
      telegramService.hapticNotification("warning");
      await loadUsers();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Ошибка аннулирования подписки");
    }
  };

  return (
    <div className="page-container">
      {error && <ErrorBanner message={error} onRetry={loadUsers} />}
      {statusMsg && (
        <div
          className="card"
          style={{
            background: "rgba(16, 185, 129, 0.14)",
            borderColor: "rgba(16, 185, 129, 0.4)",
            color: "#6ee7b7",
            fontWeight: 600,
          }}
        >
          {statusMsg}
        </div>
      )}

      <form className="card" onSubmit={handleGrant} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div className="card-title">🎁 Выдать / Подарить подписку</div>
        <div className="grid-2">
          <input
            className="input"
            placeholder="Telegram ID или @username"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
          />
          <select
            className="select"
            value={daysOrPlan}
            onChange={(e) => setDaysOrPlan(e.target.value)}
          >
            <option value="3d">3 дня (1 акк.)</option>
            <option value="7d">7 дней (1 акк.)</option>
            <option value="14d">14 дней (2 акк.)</option>
            <option value="31d">31 день (3 акк.)</option>
            <option value="365">365 дней (VIP)</option>
          </select>
        </div>
        <button className="btn" type="submit" disabled={granting || !target.trim()}>
          {granting ? "⏳ Выдача..." : "🎁 Активировать подписку"}
        </button>
      </form>

      <div className="card-title" style={{ padding: "0 4px" }}>
        <span>👥 Пользователи системы ({users.length})</span>
      </div>

      {loading ? (
        <LoadingSkeleton count={3} height={90} />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {users.map((u) => (
            <div key={u.user_id} className="card">
              <div className="card-title">
                <div>
                  <span>{u.username ? `@${u.username}` : `ID ${u.telegram_id}`}</span>
                  <span style={{ fontSize: 11, color: "var(--hint-color)", marginLeft: 6 }}>
                    (TG: {u.telegram_id})
                  </span>
                </div>
                <span className="badge">
                  {u.sub_info.is_active ? `✅ ${u.sub_info.remaining_str}` : "❌ Нет подписки"}
                </span>
              </div>

              <div style={{ fontSize: 12, color: "var(--hint-color)" }}>
                📱 Аккаунтов: <b>{u.active_accounts} акт. / {u.total_accounts} всего</b> (лимит: {u.sub_info.max_accounts})
              </div>

              {u.accounts.length > 0 && (
                <div style={{ marginTop: 6, fontSize: 12, display: "flex", flexDirection: "column", gap: 4 }}>
                  {u.accounts.map((acc) => (
                    <div key={acc.id}>
                      • {acc.is_active ? "🟢" : "⏸"} <b>{acc.first_name || "Аккаунт"}</b>{" "}
                      {acc.username && `(@${acc.username})`} — {acc.style_title}
                    </div>
                  ))}
                </div>
              )}

              {u.sub_info.is_active && (
                <button
                  className="btn btn-danger"
                  style={{ marginTop: 8, padding: "6px 10px", fontSize: 11 }}
                  onClick={() => handleCancelUserSub(u.user_id)}
                >
                  ❌ Аннулировать подписку
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
