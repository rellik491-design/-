import React from "react";

export const LoadingSkeleton: React.FC<{ count?: number; height?: number }> = ({
  count = 3,
  height = 68,
}) => {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {Array.from({ length: count }).map((_, idx) => (
        <div key={idx} className="skeleton" style={{ height }} />
      ))}
    </div>
  );
};
