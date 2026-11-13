import React from "react";

interface ErrorBannerProps {
  message: string;
  onRetry?: () => void;
}

export const ErrorBanner: React.FC<ErrorBannerProps> = ({ message, onRetry }) => {
  return (
    <div
      className="card"
      style={{
        borderColor: "rgba(244, 63, 94, 0.45)",
        background: "rgba(244, 63, 94, 0.12)",
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <div style={{ fontWeight: 700, color: "#fda4af" }}>⚠️ {message}</div>
      {onRetry && (
        <div>
          <button className="btn btn-secondary" onClick={onRetry}>
            🔄 Повторить
          </button>
        </div>
      )}
    </div>
  );
};
