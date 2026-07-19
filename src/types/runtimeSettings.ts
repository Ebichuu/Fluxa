export type RuntimeSettingFieldType = 'text' | 'url' | 'number' | 'boolean' | 'secret';

export interface RuntimeSettingField {
  key: string;
  label: string;
  type: RuntimeSettingFieldType;
  secret: boolean;
  value: string;
  hasValue: boolean;
  restartRequired: boolean;
  description: string;
}

export interface RuntimeSettingGroup {
  id: string;
  title: string;
  note: string;
  fields: RuntimeSettingField[];
}

export interface RuntimeSettingsResponse {
  success: boolean;
  groups: RuntimeSettingGroup[];
  changedKeys?: string[];
  restartRequired?: string[];
  message?: string;
}

export interface RuntimeSettingsUpdate {
  values: Record<string, string | boolean>;
  clearSecrets: string[];
}
