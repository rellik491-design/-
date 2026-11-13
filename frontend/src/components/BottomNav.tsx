import React from "react";
import { TabKey } from "../types/api";

interface BottomNavProps {
  activeTab: TabKey;
  onSelectTab: (tab: TabKey) => void;
  isAdmin: boolean;
}

export const BottomNav: React.FC<BottomNavProps> = ({ activeTab, onSelectTab, isAdmin }) => {
  const items: Array<{ key: TabKey; icon: string; label: string }> = [
    { key: "dashboard", icon: "📊", label: "Сводка" },
    { key: "chats", icon: "💬", label: "Диалоги" },
    { key: "persona", icon: "👤", label: "Анкета" },
    { key: "accounts", icon: "📱", label: "Аккаунты" },
    { key: "voice", icon: "🎙", label: "Голос" },
    { key: "subscription", icon: "⭐", label: "Тариф" },
  ];

  if (isAdmin) {
    items.push({ key: "admin", icon: "👑", label: "Админ" });
  }

  return (
    <nav className="bottom-nav">
      {items.map((item) => (
        <button
          key={item.key}
          className={`nav-item ${activeTab === item.key ? "active" : ""}`}
          onClick={() => onSelectTab(item.key)}
        >
          <span style={{ fontSize: 17 }}>{item.icon}</span>
          <span>{item.label}</span>
        </button>
      ))}
    </nav>
  );
};
