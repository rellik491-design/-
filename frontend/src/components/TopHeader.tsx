import React from "react";
import { AuthSessionResponse, TabKey } from "../types/api";

interface TopHeaderProps {
  session: AuthSessionResponse | null;
  onNavigateSub: (tab: TabKey) => void;
}

export const TopHeader: React.FC<TopHeaderProps> = ({ session, onNavigateSub }) => {
  const isSubActive = session?.subscription?.is_active;
  const isAdmin = session?.user?.is_admin;

  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "10px 14px",
        background: "var(--secondary-bg-color)",
        borderBottom: "1px solid var(--border-color)",
        position: "sticky",
        top: 0,
        zIndex: 90,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 20 }}>🌸</span>
        <div>
          <div style={{ fontWeight: 800, fontSize: 14, display: "flex", alignItems: "center", gap: 6 }}>
            AI-Компаньон
            {isAdmin && (
              <span
                className="badge"
                style={{ background: "rgba(245, 158, 11, 0.2)", color: "#fcd34d" }}
              >
                👑 Admin
              </span>
            )}
          </div>
          <div style={{ fontSize: 11, color: "var(--hint-color)" }}>
            {session?.ai_online ? "🟢 Gemini AI Online" : "🟡 AI Engine Ready"}
          </div>
        </div>
      </div>

      <button
        onClick={() => onNavigateSub("subscription")}
        className="badge"
        style={{
          border: "none",
          cursor: "pointer",
          padding: "5px 10px",
          background: isSubActive ? "rgba(16, 185, 129, 0.18)" : "rgba(244, 63, 94, 0.18)",
          color: isSubActive ? "#6ee7b7" : "#fda4af",
        }}
      >
        {isSubActive
          ? `⭐ ${session?.subscription.remaining_str}`
          : "❌ Нет подписки"}
      </button>
    </header>
  );
};
