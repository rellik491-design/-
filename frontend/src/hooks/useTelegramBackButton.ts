import { useEffect } from "react";
import { telegramService } from "../services/telegram";

export function useTelegramBackButton(visible: boolean, onBack: () => void): void {
  useEffect(() => {
    return telegramService.setBackButton(visible, onBack);
  }, [visible, onBack]);
}
