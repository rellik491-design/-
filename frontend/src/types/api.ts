export type TabKey =
  | "dashboard"
  | "chats"
  | "persona"
  | "accounts"
  | "voice"
  | "subscription"
  | "admin";

export interface UserProfile {
  id: number;
  telegram_id: number;
  username?: string | null;
  first_name?: string | null;
  is_admin: boolean;
  start_param?: string | null;
}

export interface SubscriptionSummary {
  is_active: boolean;
  plan?: string | null;
  plan_name: string;
  expires_at_str?: string | null;
  remaining_str: string;
  days_left: number;
  max_accounts: number;
  max_daily_voice: number;
  voice_used_today: number;
}

export interface AuthSessionResponse {
  session_token: string;
  user: UserProfile;
  subscription: SubscriptionSummary;
  bot_username: string;
  ai_online: boolean;
}

export interface DashboardStats {
  period: string;
  period_title: string;
  total_leads: number;
  active_leads: number;
  paused_leads: number;
  blocked_leads: number;
  meetup_leads: number;
  quest_leads: number;
  insult_leads: number;
  total_messages_in: number;
  total_messages_out: number;
  incoming_voice_count: number;
  outgoing_voice_count: number;
}

export interface ContactItem {
  id: number;
  tg_id: number;
  username?: string | null;
  first_name?: string | null;
  status: string;
  funnel_stage: string;
  detected_city?: string | null;
  detected_timezone?: string | null;
  origin_city?: string | null;
  language: string;
  total_messages_in: number;
  total_messages_out: number;
  last_incoming_at?: string | null;
  last_outgoing_at?: string | null;
  ai_active: boolean;
  bot_paused: boolean;
  paused_reason?: string | null;
  no_brackets: boolean;
  has_violation: boolean;
  violation_count: number;
  last_violation_text?: string | null;
  last_violation_analysis?: string | null;
  tags?: string | null;
  dialogue_summary?: string | null;
  dialogue_topics?: string | null;
  created_at?: string | null;
}

export interface ContactMessageItem {
  id: number;
  contact_id: number;
  telegram_message_id?: number | null;
  sender_role: "user" | "bot" | "model";
  text: string;
  is_voice: boolean;
  voice_duration: number;
  is_violation: boolean;
  violation_analysis?: string | null;
  is_edited: boolean;
  is_deleted: boolean;
  created_at: string;
}

export interface ContactMemoryItem {
  id: number;
  category: string;
  key: string;
  value: string;
  confidence: number;
  updated_at?: string | null;
}

export interface ConversationContextInspect {
  contact_id: number;
  summary_text: string;
  active_topics: string;
  unresolved_questions: string;
  commitments: string;
  memories: ContactMemoryItem[];
}

export interface PersonaItem {
  id: number;
  account_id?: number | null;
  account_label: string;
  name: string;
  age: number;
  city: string;
  district?: string | null;
  birth_city?: string | null;
  move_reason?: string | null;
  years_in_city?: string | null;
  dating_app_context?: string | null;
  study_work_hobbies?: string | null;
  extra_facts?: string | null;
  legend: string;
  timezone_name: string;
  sleep_start: string;
  sleep_end: string;
  current_style: string;
}

export interface AccountItem {
  id: number;
  first_name?: string | null;
  username?: string | null;
  phone?: string | null;
  tg_user_id?: number | null;
  business_connection_id?: string | null;
  auth_type: string;
  is_active: boolean;
  status: string;
  persona_id?: number | null;
  persona_name?: string | null;
  persona_city?: string | null;
  persona_style?: string | null;
  created_at?: string | null;
}

export interface InviteLinkResponse {
  token: string;
  deep_link: string;
  plan: string;
  expires_at: string;
}

export interface InvoiceResponse {
  invoice_id: string;
  plan: string;
  amount: number;
  asset: string;
  pay_url: string;
  status: string;
}

export interface AdminUserOverview {
  user_id: number;
  telegram_id: number;
  username?: string | null;
  total_accounts: number;
  active_accounts: number;
  sub_info: SubscriptionSummary;
  accounts: Array<{
    id: number;
    first_name?: string | null;
    username?: string | null;
    is_active: boolean;
    style_title: string;
  }>;
}
