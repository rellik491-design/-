import React from "react";
import { useAppState } from "./store/useAppStore";
import { TopHeader } from "./components/TopHeader";
import { BottomNav } from "./components/BottomNav";
import { LoadingSkeleton } from "./components/LoadingSkeleton";
import { ErrorBanner } from "./components/ErrorBanner";
import { DashboardPage } from "./pages/DashboardPage";
import { ChatsPage } from "./pages/ChatsPage";
import { PersonaStudioPage } from "./pages/PersonaStudioPage";
import { AccountsPage } from "./pages/AccountsPage";
import { VoiceLabPage } from "./pages/VoiceLabPage";
import { SubscriptionPage } from "./pages/SubscriptionPage";
import { AdminPage } from "./pages/AdminPage";

export const App: React.FC = () => {
  const {
    activeTab,
    setActiveTab,
    selectedContactId,
    setSelectedContactId,
    sessionData,
    isLoadingAuth,
    authError,
    refreshSession,
  } = useAppState();

  return (
    <div className="app-shell">
      <TopHeader session={sessionData} onNavigateSub={setActiveTab} />

      {isLoadingAuth ? (
        <div className="page-container">
          <LoadingSkeleton count={4} height={85} />
        </div>
      ) : authError || !sessionData ? (
        <div className="page-container">
          <ErrorBanner
            message={authError || "Не удалось авторизовать сессию Telegram Mini App"}
            onRetry={refreshSession}
          />
        </div>
      ) : (
        <>
          {activeTab === "dashboard" && <DashboardPage onNavigateTab={setActiveTab} />}
          {activeTab === "chats" && (
            <ChatsPage
              selectedContactId={selectedContactId}
              onSelectContact={setSelectedContactId}
            />
          )}
          {activeTab === "persona" && <PersonaStudioPage />}
          {activeTab === "accounts" && (
            <AccountsPage botUsername={sessionData.bot_username} />
          )}
          {activeTab === "voice" && <VoiceLabPage />}
          {activeTab === "subscription" && (
            <SubscriptionPage
              subscription={sessionData.subscription}
              onSubscriptionUpdated={refreshSession}
            />
          )}
          {activeTab === "admin" && sessionData.user.is_admin && <AdminPage />}
        </>
      )}

      <BottomNav
        activeTab={activeTab}
        onSelectTab={(tab) => {
          if (tab !== "chats") {
            setSelectedContactId(null);
          }
          setActiveTab(tab);
        }}
        isAdmin={Boolean(sessionData?.user?.is_admin)}
      />
    </div>
  );
};
