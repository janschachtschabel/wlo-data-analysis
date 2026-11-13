/** Formen der API, von Hand gepflegt (siehe backend/wda). */
export type Value = string | { value: string; literal?: boolean };
export interface Filter { property: string; values: Value[] }
export interface Series { id: string; label: string; filters?: Filter[]; formula?: string | null }
export interface Rows {
  property: string; include?: Value[] | null; top?: number | null; other?: boolean;
  missing?: boolean; sort?: 'count' | 'label' | 'given' | null;
  groups?: { id?: string; label: string; values: Value[] }[] | null;
}
export interface QuerySpec {
  instance: string; filters?: Filter[]; rows?: Rows | null; series?: Series[];
  columns?: { property: string; top?: number } | null;
  percent?: 'none' | 'column' | 'row'; refresh?: boolean;
}
export interface Note { code: string; text: string }
export interface ResultColumn { id: string; label: string; derived: boolean; total: number | null }
export interface ResultRow {
  key: string; label: string; kind: 'value' | 'group' | 'other' | 'missing' | 'total';
  values: (number | null)[]; percent?: (number | null)[] | null; level?: number;
}
export interface QueryResult {
  schema_version: number; instance: string; columns: ResultColumn[]; rows: ResultRow[];
  provenance: {
    fetched_at: number | null; oldest_fetched_at: number | null; cached: boolean;
    queries: number; notes: Note[];
    filters: { property: string; values: { value: string; label?: string }[] }[];
  };
}
export interface Instance {
  id: string; name: string; base_url: string; search_url?: string | null;
  has_credentials: boolean;
}
export interface FieldInfo { property: string; caption: string; type: string; value_count: number }
export interface VocabValue { id: string; caption: string; parent?: string | null }
export interface VocabPage { property: string; caption: string; total: number; values: VocabValue[] }
export type ViewType = 'table' | 'number' | 'bar' | 'stacked_bar' | 'line' | 'pie';
export interface View { type: ViewType; options?: Record<string, string | number | boolean | null> }
export type Kind = 'filter' | 'metric' | 'dashboard' | 'template';
export interface Definition {
  id: string; kind: Kind; name: string; description: string; group_id: string | null;
  version: number; created_at: string; updated_at: string; spec?: any; used_by?: any[];
}
export interface Group { id: string; name: string }
export interface RunHead {
  id: string | null; seq?: number; definition_id: string | null; kind: Kind; name: string;
  status: 'ok' | 'partial' | 'failed'; started_at: string; finished_at: string;
}
export interface ReportTable {
  type: 'table'; key: string; title: string;
  columns: { id: string; label: string; derived: boolean }[]; rows: ResultRow[];
  header_groups?: [string, number][] | null; view: View; notes: Note[];
  sources: Record<string, any>[];
}
export interface ReportText { type: 'text'; key: string; markdown: string; html: string }
export interface ReportFailure { type: 'failure'; key: string; title: string; detail: string; kind: string }
export type ReportBlock = ReportTable | ReportText | ReportFailure;
export interface Report {
  report_version: number; title: string; kind: Kind; run_id: string | null;
  status: 'ok' | 'partial' | 'failed'; started_at: string; finished_at: string;
  notes: Note[]; blocks: ReportBlock[];
}
export interface AppSettings {
  version: string; export: { formats: string[]; pdf_available: boolean };
  llm: { base_url: string; model: string; api_key_set: boolean };
}
