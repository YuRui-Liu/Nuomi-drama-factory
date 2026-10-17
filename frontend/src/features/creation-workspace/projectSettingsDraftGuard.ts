export interface SettingsDraftGuard {
  isDirty(): boolean;
  save(): Promise<boolean>;
  discard(): void;
}
