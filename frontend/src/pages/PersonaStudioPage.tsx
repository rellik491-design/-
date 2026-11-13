import React, { useEffect, useState, useCallback } from "react";
import { apiClient } from "../api/client";
import { PersonaItem } from "../types/api";
import { LoadingSkeleton } from "../components/LoadingSkeleton";
import { ErrorBanner } from "../components/ErrorBanner";
import { telegramService } from "../services/telegram";

export const PersonaStudioPage: React.FC = () => {
  const [personas, setPersonas] = useState<PersonaItem[]>([]);
  const [selectedIdx, setSelectedIdx] = useState<number>(0);
  const [form, setForm] = useState<Partial<PersonaItem>>({});
  const [wizardText, setWizardText] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(true);
  const [saving, setSaving] = useState<boolean>(false);
  const [wizardRunning, setWizardRunning] = useState<boolean>(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadPersonas = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await apiClient.listPersonas();
      setPersonas(list);
      if (list.length > 0) {
        const current = list[Math.min(selectedIdx, list.length - 1)];
        setForm(current);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить анкеты");
    } finally {
      setLoading(false);
    }
  }, [selectedIdx]);

  useEffect(() => {
    loadPersonas();
  }, [loadPersonas]);

  const activePersona = personas[selectedIdx] || null;

  const handleSelectPersona = (idx: number) => {
    telegramService.hapticSelection();
    setSelectedIdx(idx);
    setForm(personas[idx]);
    setStatusMessage(null);
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activePersona) return;
    setSaving(true);
    setError(null);
    setStatusMessage(null);
    try {
      const updated = await apiClient.updatePersona(activePersona.id, form);
      const next = [...personas];
      next[selectedIdx] = { ...updated, account_id: activePersona.account_id, account_label: activePersona.account_label };
      setPersonas(next);
      setForm(next[selectedIdx]);
      telegramService.hapticNotification("success");
      setStatusMessage("✅ Анкета и легенда девушки успешно сохранены!");
    } catch (err: unknown) {
      telegramService.hapticNotification("error");
      setError(err instanceof Error ? err.message : "Ошибка сохранения анкеты");
    } finally {
      setSaving(false);
    }
  };

  const handleRunWizard = async () => {
    if (!wizardText.trim() || wizardRunning) return;
    setWizardRunning(true);
    setError(null);
    setStatusMessage(null);
    try {
      const updated = await apiClient.runPersonaWizard(
        wizardText.trim(),
        activePersona?.account_id
      );
      setWizardText("");
      telegramService.hapticNotification("success");
      setStatusMessage("✨ ИИ автоматически заполнил все поля легенды!");
      await loadPersonas();
      setForm(updated);
    } catch (err: unknown) {
      telegramService.hapticNotification("error");
      setError(err instanceof Error ? err.message : "Ошибка авто-заполнения анкеты");
    } finally {
      setWizardRunning(false);
    }
  };

  if (loading) {
    return (
      <div className="page-container">
        <LoadingSkeleton count={4} height={90} />
      </div>
    );
  }

  return (
    <div className="page-container">
      {personas.length > 1 && (
        <div className="chip-row">
          {personas.map((p, idx) => (
            <button
              key={p.id}
              className={`chip ${selectedIdx === idx ? "active" : ""}`}
              onClick={() => handleSelectPersona(idx)}
            >
              👩 {p.account_label}
            </button>
          ))}
        </div>
      )}

      {error && <ErrorBanner message={error} onRetry={loadPersonas} />}
      {statusMessage && (
        <div
          className="card"
          style={{
            background: "rgba(16, 185, 129, 0.14)",
            borderColor: "rgba(16, 185, 129, 0.4)",
            color: "#6ee7b7",
            fontWeight: 600,
          }}
        >
          {statusMessage}
        </div>
      )}

      <div className="card">
        <div className="card-title">✨ Умное AI-заполнение легенды в 1 клик</div>
        <div style={{ fontSize: 12, color: "var(--hint-color)", marginBottom: 8 }}>
          Опишите девушку свободным текстом (имя, возраст, город, откуда переехала, на кого учится/работает, хобби), и нейросеть сама структурирует анкету:
        </div>
        <textarea
          className="textarea"
          rows={3}
          placeholder="Например: Меня зовут Катя, 23 года, живу в Казани на Баумана, переехала из Самары учиться на дизайнера 3 года назад, люблю кофе и квесты..."
          value={wizardText}
          onChange={(e) => setWizardText(e.target.value)}
        />
        <button
          className="btn"
          style={{ marginTop: 8, width: "100%" }}
          disabled={wizardRunning || wizardText.trim().length < 5}
          onClick={handleRunWizard}
        >
          {wizardRunning ? "⏳ Нейросеть разбирает легенду..." : "🪄 Заполнить анкету через ИИ"}
        </button>
      </div>

      {activePersona && (
        <form className="card" onSubmit={handleSave} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div className="card-title">
            <span>👤 Анкета и Легенда ({activePersona.name})</span>
            <span className="badge">🕒 {activePersona.timezone_name}</span>
          </div>

          <div className="grid-2">
            <div>
              <label style={{ fontSize: 11, color: "var(--hint-color)" }}>Имя девушки</label>
              <input
                className="input"
                value={form.name || ""}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </div>
            <div>
              <label style={{ fontSize: 11, color: "var(--hint-color)" }}>Возраст</label>
              <input
                className="input"
                type="number"
                min={18}
                max={60}
                value={form.age || 22}
                onChange={(e) => setForm({ ...form, age: Number(e.target.value) })}
              />
            </div>
          </div>

          <div className="grid-2">
            <div>
              <label style={{ fontSize: 11, color: "var(--hint-color)" }}>Город проживания</label>
              <input
                className="input"
                value={form.city || ""}
                onChange={(e) => setForm({ ...form, city: e.target.value })}
              />
            </div>
            <div>
              <label style={{ fontSize: 11, color: "var(--hint-color)" }}>Район / Метро</label>
              <input
                className="input"
                value={form.district || ""}
                onChange={(e) => setForm({ ...form, district: e.target.value })}
              />
            </div>
          </div>

          <div className="grid-2">
            <div>
              <label style={{ fontSize: 11, color: "var(--hint-color)" }}>Откуда родом</label>
              <input
                className="input"
                value={form.birth_city || ""}
                onChange={(e) => setForm({ ...form, birth_city: e.target.value })}
              />
            </div>
            <div>
              <label style={{ fontSize: 11, color: "var(--hint-color)" }}>Сколько уже живет тут</label>
              <input
                className="input"
                value={form.years_in_city || ""}
                onChange={(e) => setForm({ ...form, years_in_city: e.target.value })}
              />
            </div>
          </div>

          <div>
            <label style={{ fontSize: 11, color: "var(--hint-color)" }}>Почему переехала</label>
            <input
              className="input"
              value={form.move_reason || ""}
              onChange={(e) => setForm({ ...form, move_reason: e.target.value })}
            />
          </div>

          <div>
            <label style={{ fontSize: 11, color: "var(--hint-color)" }}>Где познакомились (дейтинг-контекст)</label>
            <textarea
              className="textarea"
              rows={2}
              value={form.dating_app_context || ""}
              onChange={(e) => setForm({ ...form, dating_app_context: e.target.value })}
            />
          </div>

          <div>
            <label style={{ fontSize: 11, color: "var(--hint-color)" }}>Учеба, работа и увлечения</label>
            <textarea
              className="textarea"
              rows={2}
              value={form.study_work_hobbies || ""}
              onChange={(e) => setForm({ ...form, study_work_hobbies: e.target.value })}
            />
          </div>

          <div>
            <label style={{ fontSize: 11, color: "var(--hint-color)" }}>Дополнительные факты и отношение к свиданиям</label>
            <textarea
              className="textarea"
              rows={2}
              value={form.extra_facts || ""}
              onChange={(e) => setForm({ ...form, extra_facts: e.target.value })}
            />
          </div>

          <div className="grid-2">
            <div>
              <label style={{ fontSize: 11, color: "var(--hint-color)" }}>Сон с (ЧЧ:ММ)</label>
              <input
                className="input"
                value={form.sleep_start || "23:00"}
                onChange={(e) => setForm({ ...form, sleep_start: e.target.value })}
              />
            </div>
            <div>
              <label style={{ fontSize: 11, color: "var(--hint-color)" }}>Сон до (ЧЧ:ММ)</label>
              <input
                className="input"
                value={form.sleep_end || "09:00"}
                onChange={(e) => setForm({ ...form, sleep_end: e.target.value })}
              />
            </div>
          </div>

          <div>
            <label style={{ fontSize: 11, color: "var(--hint-color)" }}>Стиль общения ИИ</label>
            <select
              className="select"
              value={form.current_style || "adaptive_all"}
              onChange={(e) => setForm({ ...form, current_style: e.target.value })}
            >
              <option value="adaptive_all">🎭 Единый Адаптивный (Рекомендуется)</option>
              <option value="playful">🔥 Игривая и кокетливая</option>
              <option value="shy">🌸 Скромная и нежная</option>
              <option value="sarcastic">⚡ Дерзкая с сарказмом</option>
              <option value="caring">☕ Заботливая и теплая</option>
            </select>
          </div>

          <button className="btn" type="submit" disabled={saving}>
            {saving ? "💾 Сохранение..." : "💾 Сохранить изменения анкеты"}
          </button>
        </form>
      )}
    </div>
  );
};
