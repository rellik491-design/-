import {
  AccountItem,
  AdminUserOverview,
  AuthSessionResponse,
  ContactItem,
  ContactMessageItem,
  ConversationContextInspect,
  DashboardStats,
  InviteLinkResponse,
  InvoiceResponse,
  PersonaItem,
  SubscriptionSummary,
} from "../types/api";
import { telegramService } from "../services/telegram";

const SESSION_TOKEN_STORAGE_KEY = "tg_companion_session_token";

class ApiClient {
  private sessionToken: string | null = sessionStorage.getItem(SESSION_TOKEN_STORAGE_KEY);

  public setSessionToken(token: string | null): void {
    this.sessionToken = token;
    if (token) {
      sessionStorage.setItem(SESSION_TOKEN_STORAGE_KEY, token);
    } else {
      sessionStorage.removeItem(SESSION_TOKEN_STORAGE_KEY);
    }
  }

  public getSessionToken(): string | null {
    return this.sessionToken;
  }

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(options.headers as Record<string, string> | undefined),
    };

    const initData = telegramService.getSignedInitData();
    if (initData) {
      headers["X-Telegram-Init-Data"] = initData;
    }
    if (this.sessionToken) {
      headers["Authorization"] = `Bearer ${this.sessionToken}`;
    }

    const res = await fetch(path, {
      ...options,
      headers,
    });

    if (!res.ok) {
      let errMsg = `Ошибка запроса (${res.status})`;
      try {
        const errJson = await res.json();
        errMsg = errJson?.error?.message || errJson?.detail || errMsg;
      } catch {
        // Ignore JSON parse error
      }
      throw new Error(errMsg);
    }

    return (await res.json()) as T;
  }

  public async authenticateTelegram(initData: string): Promise<AuthSessionResponse> {
    const data = await this.request<AuthSessionResponse>("/api/v1/auth/telegram", {
      method: "POST",
      body: JSON.stringify({ init_data: initData }),
    });
    this.setSessionToken(data.session_token);
    return data;
  }

  public async getMe(): Promise<AuthSessionResponse> {
    const data = await this.request<AuthSessionResponse>("/api/v1/auth/me");
    this.setSessionToken(data.session_token);
    return data;
  }

  public async getDashboardStats(period: string = "today"): Promise<DashboardStats> {
    return this.request<DashboardStats>(`/api/v1/dashboard/stats?period=${encodeURIComponent(period)}`);
  }

  public async listContacts(filter: string = "all", query: string = ""): Promise<ContactItem[]> {
    const params = new URLSearchParams({ filter });
    if (query.trim()) params.set("q", query.trim());
    return this.request<ContactItem[]>(`/api/v1/contacts?${params.toString()}`);
  }

  public async getContactMessages(contactId: number): Promise<ContactMessageItem[]> {
    return this.request<ContactMessageItem[]>(`/api/v1/contacts/${contactId}/messages?limit=80`);
  }

  public async getContactContext(contactId: number): Promise<ConversationContextInspect> {
    return this.request<ConversationContextInspect>(`/api/v1/contacts/${contactId}/context`);
  }

  public async pauseContact(contactId: number): Promise<ContactItem> {
    return this.request<ContactItem>(`/api/v1/contacts/${contactId}/pause`, { method: "POST" });
  }

  public async resumeContact(contactId: number): Promise<ContactItem> {
    return this.request<ContactItem>(`/api/v1/contacts/${contactId}/resume`, { method: "POST" });
  }

  public async blockContact(contactId: number): Promise<ContactItem> {
    return this.request<ContactItem>(`/api/v1/contacts/${contactId}/block`, { method: "POST" });
  }

  public async toggleContactTag(contactId: number, tagCode: string): Promise<ContactItem> {
    return this.request<ContactItem>(`/api/v1/contacts/${contactId}/tags`, {
      method: "POST",
      body: JSON.stringify({ tag_code: tagCode }),
    });
  }

  public async sendOrSimulateMessage(
    contactId: number,
    text: string,
    mode: "owner_manual" | "simulate_incoming" = "owner_manual"
  ): Promise<{ status: string; reply_text?: string; contact: ContactItem }> {
    return this.request(`/api/v1/contacts/${contactId}/send`, {
      method: "POST",
      body: JSON.stringify({ text, mode }),
    });
  }

  public async deleteContact(contactId: number): Promise<{ status: string; id: number }> {
    return this.request(`/api/v1/contacts/${contactId}`, { method: "DELETE" });
  }

  public async listPersonas(): Promise<PersonaItem[]> {
    return this.request<PersonaItem[]>("/api/v1/personas");
  }

  public async updatePersona(personaId: number, payload: Partial<PersonaItem>): Promise<PersonaItem> {
    return this.request<PersonaItem>(`/api/v1/personas/${personaId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  }

  public async runPersonaWizard(description: string, accountId?: number | null): Promise<PersonaItem> {
    return this.request<PersonaItem>("/api/v1/personas/wizard", {
      method: "POST",
      body: JSON.stringify({ description, account_id: accountId ?? null }),
    });
  }

  public async listAccounts(): Promise<AccountItem[]> {
    return this.request<AccountItem[]>("/api/v1/accounts");
  }

  public async toggleAccount(accountId: number): Promise<AccountItem> {
    return this.request<AccountItem>(`/api/v1/accounts/${accountId}/toggle`, { method: "POST" });
  }

  public async deleteAccount(accountId: number): Promise<{ status: string }> {
    return this.request(`/api/v1/accounts/${accountId}`, { method: "DELETE" });
  }

  public async createInviteLink(planCode: string): Promise<InviteLinkResponse> {
    return this.request<InviteLinkResponse>("/api/v1/invites", {
      method: "POST",
      body: JSON.stringify({ plan_code: planCode }),
    });
  }

  public async getSubscription(): Promise<SubscriptionSummary> {
    return this.request<SubscriptionSummary>("/api/v1/subscriptions/current");
  }

  public async createInvoice(planCode: string): Promise<InvoiceResponse> {
    return this.request<InvoiceResponse>("/api/v1/subscriptions/invoice", {
      method: "POST",
      body: JSON.stringify({ plan_code: planCode }),
    });
  }

  public async checkInvoice(invoiceId: string): Promise<{
    invoice_id: string;
    is_paid: boolean;
    status: string;
    subscription: SubscriptionSummary;
  }> {
    return this.request(`/api/v1/subscriptions/check-invoice/${encodeURIComponent(invoiceId)}`, {
      method: "POST",
    });
  }

  public async cancelSubscription(): Promise<{ cancelled: boolean; subscription: SubscriptionSummary }> {
    return this.request("/api/v1/subscriptions/cancel", { method: "POST" });
  }

  public async testVoice(
    style: string,
    customText?: string,
    sendToTelegram: boolean = true
  ): Promise<{
    status: string;
    style: string;
    raw_text: string;
    prepared_phonetics: string;
    sent_to_telegram: boolean;
  }> {
    return this.request("/api/v1/voice/test", {
      method: "POST",
      body: JSON.stringify({
        style,
        custom_text: customText || null,
        send_to_telegram: sendToTelegram,
      }),
    });
  }

  public async adminListUsers(): Promise<AdminUserOverview[]> {
    return this.request<AdminUserOverview[]>("/api/v1/admin/users");
  }

  public async adminGrantSubscription(target: string, daysOrPlan: string): Promise<{
    status: string;
    telegram_id: number;
    days_granted: number;
  }> {
    return this.request("/api/v1/admin/grant", {
      method: "POST",
      body: JSON.stringify({ target, days_or_plan: daysOrPlan }),
    });
  }

  public async adminCancelUserSubscription(targetUserId: number): Promise<{ status: string }> {
    return this.request(`/api/v1/admin/users/${targetUserId}/cancel-sub`, {
      method: "POST",
    });
  }
}

export const apiClient = new ApiClient();
