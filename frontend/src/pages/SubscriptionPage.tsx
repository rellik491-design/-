import React, { useState } from "react";
import { apiClient } from "../api/client";
import { InvoiceResponse, SubscriptionSummary } from "../types/api";
import { ErrorBanner } from "../components/ErrorBanner";
import { telegramService } from "../services/telegram";

interface SubscriptionPageProps {
  subscription: SubscriptionSummary;
  onSubscriptionUpdated: () => void;
}

export const SubscriptionPage: React.FC<SubscriptionPageProps> = ({
  subscription,
  onSubscriptionUpdated,
}) => {
  const [invoice, setInvoice] = useState<InvoiceResponse | null>(null);
  const [loadingPlan, setLoadingPlan] = useState<string | null>(null);
  const [checkingPayment, setCheckingPayment] = useState<boolean>(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const plans = [
    { code: "3d", title: "3 Дня (Тест-драйв)", price: "3 USDT", accounts: "1 аккаунт" },
    { code: "7d", title: "7 Дней (Неделя)", price: "7 USDT", accounts: "1 аккаунт" },
    { code: "14d", title: "14 Дней (Профи)", price: "14 USDT", accounts: "до 2 аккаунтов" },
    { code: "31d", title: "31 День (Максимум)", price: "31 USDT", accounts: "до 3 аккаунтов" },
  ];

  const handleCreateInvoice = async (planCode: string) => {
    setLoadingPlan(planCode);
    setError(null);
    setStatusMsg(null);
    telegramService.hapticImpact("light");
    try {
      const inv = await apiClient.createInvoice(planCode);
      setInvoice(inv);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Не удалось выставить счет");
    } finally {
      setLoadingPlan(null);
    }
  };

  const handleCheckPayment = async () => {
    if (!invoice) return;
    setCheckingPayment(true);
    setError(null);
    try {
      const res = await apiClient.checkInvoice(invoice.invoice_id);
      if (res.is_paid) {
        telegramService.hapticNotification("success");
        setStatusMsg("🎉 Оплата подтверждена! Ваша подписка активирована.");
        setInvoice(null);
        onSubscriptionUpdated();
      } else {
        telegramService.hapticNotification("warning");
        setStatusMsg(`Статус счёта: ${res.status}. Если вы уже оплатили, подождите пару секунд и нажмите снова.`);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Ошибка проверки платежа");
    } finally {
      setCheckingPayment(false);
    }
  };

  const handleCancelSubscription = async () => {
    if (!window.confirm("Вы уверены, что хотите аннулировать текущую активную подписку?")) return;
    try {
      await apiClient.cancelSubscription();
      telegramService.hapticNotification("warning");
      setStatusMsg("Подписка аннулирована.");
      onSubscriptionUpdated();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Ошибка отмены подписки");
    }
  };

  return (
    <div className="page-container">
      {error && <ErrorBanner message={error} />}
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

      <div className="card">
        <div className="card-title">
          <span>⭐ Текущий статус подписки</span>
          <span className="badge">
            {subscription.is_active ? "✅ Активна" : "❌ Не активна"}
          </span>
        </div>

        <div className="grid-2" style={{ marginTop: 8 }}>
          <div className="stat-box">
            <div className="stat-value" style={{ fontSize: 16 }}>
              {subscription.plan_name}
            </div>
            <div className="stat-label">Тарифный план</div>
          </div>
          <div className="stat-box">
            <div className="stat-value" style={{ fontSize: 16 }}>
              {subscription.remaining_str}
            </div>
            <div className="stat-label">Осталось времени</div>
          </div>
          <div className="stat-box">
            <div className="stat-value" style={{ fontSize: 16 }}>
              до {subscription.max_accounts} акк.
            </div>
            <div className="stat-label">Лимит аккаунтов</div>
          </div>
          <div className="stat-box">
            <div className="stat-value" style={{ fontSize: 16 }}>
              {subscription.voice_used_today} / {subscription.max_daily_voice}
            </div>
            <div className="stat-label">Голосовых сегодня</div>
          </div>
        </div>

        {subscription.is_active && (
          <button
            className="btn btn-danger"
            style={{ width: "100%", marginTop: 10 }}
            onClick={handleCancelSubscription}
          >
            ❌ Отменить подписку
          </button>
        )}
      </div>

      {invoice && (
        <div
          className="card"
          style={{ borderColor: "rgba(236, 72, 153, 0.5)", background: "rgba(236, 72, 153, 0.1)" }}
        >
          <div className="card-title">
            <span>💳 Счёт #{invoice.invoice_id}</span>
            <span className="badge">{invoice.amount} {invoice.asset}</span>
          </div>
          <div style={{ fontSize: 12, color: "var(--hint-color)", marginBottom: 10 }}>
            Оплатите счёт через @CryptoBot и нажмите «Проверить оплату»:
          </div>
          <div className="grid-2">
            <button
              className="btn"
              onClick={() => telegramService.openTelegramLink(invoice.pay_url)}
            >
              💎 Оплатить в CryptoBot
            </button>
            <button
              className="btn btn-success"
              disabled={checkingPayment}
              onClick={handleCheckPayment}
            >
              {checkingPayment ? "⏳ Проверка..." : "🔄 Проверить оплату"}
            </button>
          </div>
        </div>
      )}

      <div className="card">
        <div className="card-title">🚀 Продлить или выбрать тариф (@CryptoBot USDT)</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {plans.map((p) => (
            <div
              key={p.code}
              className="stat-box"
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <div>
                <div style={{ fontWeight: 700, fontSize: 14 }}>{p.title}</div>
                <div style={{ fontSize: 11, color: "var(--hint-color)" }}>
                  Лимит: {p.accounts} • <b>{p.price}</b>
                </div>
              </div>
              <button
                className="btn"
                disabled={loadingPlan !== null}
                onClick={() => handleCreateInvoice(p.code)}
              >
                {loadingPlan === p.code ? "⏳..." : `Купить (${p.price})`}
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
