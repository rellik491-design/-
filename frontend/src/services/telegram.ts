export interface TelegramWebApp {
  initData: string;
  initDataUnsafe: {
    query_id?: string;
    user?: {
      id: number;
      first_name?: string;
      last_name?: string;
      username?: string;
      language_code?: string;
    };
    auth_date?: string;
    hash?: string;
    start_param?: string;
  };
  colorScheme: "light" | "dark";
  themeParams: Record<string, string | undefined>;
  isExpanded: boolean;
  viewportHeight: number;
  viewportStableHeight: number;
  ready: () => void;
  expand: () => void;
  close: () => void;
  openTelegramLink: (url: string) => void;
  openLink: (url: string) => void;
  onEvent: (eventType: string, callback: () => void) => void;
  offEvent: (eventType: string, callback: () => void) => void;
  BackButton: {
    isVisible: boolean;
    show: () => void;
    hide: () => void;
    onClick: (cb: () => void) => void;
    offClick: (cb: () => void) => void;
  };
  MainButton: {
    text: string;
    isVisible: boolean;
    isActive: boolean;
    show: () => void;
    hide: () => void;
    enable: () => void;
    disable: () => void;
    setText: (text: string) => void;
    onClick: (cb: () => void) => void;
    offClick: (cb: () => void) => void;
  };
  HapticFeedback: {
    impactOccurred: (style: "light" | "medium" | "heavy" | "rigid" | "soft") => void;
    notificationOccurred: (type: "error" | "success" | "warning") => void;
    selectionChanged: () => void;
  };
}

declare global {
  interface Window {
    Telegram?: {
      WebApp?: TelegramWebApp;
    };
  }
}

class TelegramWebAppService {
  private get webapp(): TelegramWebApp | undefined {
    return window.Telegram?.WebApp;
  }

  public init(): void {
    const tg = this.webapp;
    if (!tg) {
      document.documentElement.setAttribute("data-theme", "dark");
      return;
    }

    try {
      tg.ready();
      tg.expand();
      this.applyTheme();
      tg.onEvent("themeChanged", () => this.applyTheme());
      tg.onEvent("viewportChanged", () => this.applyViewport());
      this.applyViewport();
    } catch (err) {
      console.warn("Telegram WebApp initialization warning:", err);
    }
  }

  public getSignedInitData(): string {
    return this.webapp?.initData || "";
  }

  public getStartParam(): string | undefined {
    return this.webapp?.initDataUnsafe?.start_param;
  }

  public applyTheme(): void {
    const tg = this.webapp;
    const scheme = tg?.colorScheme || "dark";
    document.documentElement.setAttribute("data-theme", scheme);

    const params = tg?.themeParams || {};
    const root = document.documentElement.style;
    if (params.bg_color) root.setProperty("--tg-theme-bg-color", params.bg_color);
    if (params.secondary_bg_color) root.setProperty("--tg-theme-secondary-bg-color", params.secondary_bg_color);
    if (params.text_color) root.setProperty("--tg-theme-text-color", params.text_color);
    if (params.hint_color) root.setProperty("--tg-theme-hint-color", params.hint_color);
    if (params.link_color) root.setProperty("--tg-theme-link-color", params.link_color);
    if (params.button_color) root.setProperty("--tg-theme-button-color", params.button_color);
    if (params.button_text_color) root.setProperty("--tg-theme-button-text-color", params.button_text_color);
  }

  public applyViewport(): void {
    const tg = this.webapp;
    if (tg?.viewportStableHeight) {
      document.documentElement.style.setProperty(
        "--tg-viewport-stable-height",
        `${tg.viewportStableHeight}px`
      );
    }
  }

  public hapticImpact(style: "light" | "medium" | "heavy" = "light"): void {
    try {
      this.webapp?.HapticFeedback?.impactOccurred(style);
    } catch {
      // Ignore outside Telegram environment
    }
  }

  public hapticNotification(type: "success" | "warning" | "error"): void {
    try {
      this.webapp?.HapticFeedback?.notificationOccurred(type);
    } catch {
      // Ignore outside Telegram environment
    }
  }

  public hapticSelection(): void {
    try {
      this.webapp?.HapticFeedback?.selectionChanged();
    } catch {
      // Ignore outside Telegram environment
    }
  }

  public setBackButton(visible: boolean, onClick?: () => void): () => void {
    const btn = this.webapp?.BackButton;
    if (!btn) return () => {};
    if (visible && onClick) {
      btn.show();
      btn.onClick(onClick);
      return () => {
        btn.offClick(onClick);
        btn.hide();
      };
    } else {
      btn.hide();
      return () => {};
    }
  }

  public setMainButton(text: string, visible: boolean, onClick?: () => void): () => void {
    const btn = this.webapp?.MainButton;
    if (!btn) return () => {};
    if (visible && onClick) {
      btn.setText(text);
      btn.show();
      btn.enable();
      btn.onClick(onClick);
      return () => {
        btn.offClick(onClick);
        btn.hide();
      };
    } else {
      btn.hide();
      return () => {};
    }
  }

  public openTelegramLink(url: string): void {
    if (this.webapp?.openTelegramLink && url.startsWith("https://t.me/")) {
      this.webapp.openTelegramLink(url);
    } else if (this.webapp?.openLink) {
      this.webapp.openLink(url);
    } else {
      window.open(url, "_blank", "noopener,noreferrer");
    }
  }

  public close(): void {
    this.webapp?.close();
  }
}

export const telegramService = new TelegramWebAppService();
