export type AssetImportType = "character" | "scene" | "prop";
export type AssetImportDisposition = "create" | "supplement" | "skip" | "conflict";

export interface AssetImportFieldDiff {
  field: string;
  current?: unknown;
  proposed?: unknown;
  disposition: "fill" | "preserve" | "conflict";
}

export interface AssetImportDiff {
  name: string;
  disposition: AssetImportDisposition;
  changes: AssetImportFieldDiff[];
  warnings?: string[];
}

export interface AssetImportPreview {
  import_id: string;
  asset_type: AssetImportType;
  filename?: string;
  diffs: AssetImportDiff[];
  ignored_sections?: string[];
  warnings?: string[];
  created_count?: number;
  supplemented_count?: number;
  skipped_count?: number;
  warning_count?: number;
}

export interface AssetImportResult {
  import_id?: string;
  created_count?: number;
  supplemented_count?: number;
  skipped_count?: number;
  warning_count?: number;
  warnings?: string[];
  diffs?: AssetImportDiff[];
  message?: string;
}
