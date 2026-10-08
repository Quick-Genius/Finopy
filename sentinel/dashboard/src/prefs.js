// Client-side preferences only (this browser). Server behaviour is configured in .env, never from the UI.
const KEY = 'sentinel.prefs';
export const DEFAULT_PREFS = { theme: 'system', density: 'comfortable', refreshMs: 4000 };

export const loadPrefs = () => {
  try { return { ...DEFAULT_PREFS, ...JSON.parse(localStorage.getItem(KEY)) }; } catch { return { ...DEFAULT_PREFS }; }
};
export const savePrefs = (p) => { try { localStorage.setItem(KEY, JSON.stringify(p)); } catch { /* storage blocked: prefs last for this tab only */ } };
export const applyPrefs = (p) => {
  document.documentElement.dataset.theme = p.theme;
  document.documentElement.dataset.density = p.density;
};
