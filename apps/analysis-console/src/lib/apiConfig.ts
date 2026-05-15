import type { AdminApiConfig } from '../types/reports';

const KEY = 'bookcraft_analysis_console_api_config_v2';

export const defaultApiConfig: AdminApiConfig = {
  enabled: false,
  baseUrl: 'http://localhost:8000',
  token: ''
};

export function loadApiConfig(): AdminApiConfig {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return defaultApiConfig;
    return { ...defaultApiConfig, ...JSON.parse(raw) };
  } catch {
    return defaultApiConfig;
  }
}

export function saveApiConfig(config: AdminApiConfig): void {
  localStorage.setItem(KEY, JSON.stringify(config));
}
