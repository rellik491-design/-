import { useState, useEffect, useCallback } from "react";
import { AuthSessionResponse, TabKey } from "../types/api";
import { apiClient } from "../api/client";
import { telegramService } from "../services/telegram";

const ACTIVE_TAB_STORAGE_KEY = "tg_companion_active_tab";
const SELECTED_CONTACT_STORAGE_KEY = "tg_companion_selected_contact";

export function useAppState() {
  const [activeTab, setActiveTabState] = useState<TabKey>(() => {
    const saved = sessionStorage.getItem(ACTIVE_TAB_STORAGE_KEY) as TabKey | null;
    return saved || "dashboard";
  });

  const [selectedContactId, setSelectedContactIdState] = useState<number | null>(() => {
    const saved = sessionStorage.getItem(SELECTED_CONTACT_STORAGE_KEY);
    return saved ? Number(saved) : null;
  });

  const [sessionData, setSessionData] = useState<AuthSessionResponse | null>(null);
  const [isLoadingAuth, setIsLoadingAuth] = useState<boolean>(true);
  const [authError, setAuthError] = useState<string | null>(null);

  const setActiveTab = useCallback((tab: TabKey) => {
    telegramService.hapticSelection();
    setActiveTabState(tab);
    sessionStorage.setItem(ACTIVE_TAB_STORAGE_KEY, tab);
  }, []);

  const setSelectedContactId = useCallback((id: number | null) => {
    setSelectedContactIdState(id);
    if (id !== null) {
      sessionStorage.setItem(SELECTED_CONTACT_STORAGE_KEY, String(id));
    } else {
      sessionStorage.removeItem(SELECTED_CONTACT_STORAGE_KEY);
    }
  }, []);

  const refreshSession = useCallback(async () => {
    setIsLoadingAuth(true);
    setAuthError(null);
    try {
      const initData = telegramService.getSignedInitData();
      if (initData) {
        const res = await apiClient.authenticateTelegram(initData);
        setSessionData(res);
      } else if (apiClient.getSessionToken()) {
        const res = await apiClient.getMe();
        setSessionData(res);
      } else {
        setAuthError(
          "Откройте приложение внутри Telegram через кнопку Mini App в боте (требуется подписанная Telegram initData)."
        );
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Ошибка авторизации Telegram Mini App";
      setAuthError(msg);
    } finally {
      setIsLoadingAuth(false);
    }
  }, []);

  useEffect(() => {
    telegramService.init();
    refreshSession();
  }, [refreshSession]);

  return {
    activeTab,
    setActiveTab,
    selectedContactId,
    setSelectedContactId,
    sessionData,
    setSessionData,
    isLoadingAuth,
    authError,
    refreshSession,
  };
}
