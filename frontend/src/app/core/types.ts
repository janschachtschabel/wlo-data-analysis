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
  /** Vergleich mit einem früheren Lauf: dessen Werte; `new`: die Zeile gab es dort nicht */
  previous?: (number | null)[] | null; new?: boolean;
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
  repository?: string; metadataset?: string; query?: string; facet_fields?: string[];
  source?: 'preset' | 'config' | 'stored'; overridden?: boolean;
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
  last_run?: { id: string; status: string; started_at: string } | null;
}
export interface Group { id: string; name: string }
export interface RunHead {
  id: string | null; seq?: number; definition_id: string | null; kind: Kind; name: string;
  status: 'ok' | 'partial' | 'failed'; started_at: string; finished_at: string;
  origin?: 'manual' | 'schedule'; instance?: string | null;
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
  definition_id?: string | null; compare?: { run_id: string; started_at: string } | null;
}
export interface AppSettings {
  version: string; export: { formats: string[]; pdf_available: boolean };
  llm: { base_url: string; model: string; api_key_set: boolean };
}
export interface Schedule {
  id: string; definition_id: string; definition_name: string; definition_kind: Kind;
  instance: string | null; every: 'daily' | 'weekly' | 'monthly'; at: string;
  weekday: number | null; day: number | null; enabled: boolean; next_due: string;
  attempts: number; last_run_id: string | null; last_status: string | null;
  last_error: string | null; last_started_at: string | null;
}
export interface HistoryKeys {
  run_id: string | null;
  blocks: { key: string; title: string; columns: { id: string; label: string }[];
    rows: { key: string; label: string; kind: string }[] }[];
}
export interface HistoryPoint {
  run_id: string; started_at: string; status: string; definition_version: number;
  origin: string; values: (number | null)[];
}
export interface HistoryData {
  block?: { key: string; title: string };
  series: { key: string; row: string; column: string; label: string }[];
  points: HistoryPoint[]; notes: Note[];
}
/** Antwort auf einen als Auftrag gestarteten Lauf, solange er läuft oder gescheitert ist. */
export interface JobState {
  id: string; status: 'running' | 'failed' | 'ok' | 'partial'; saved?: boolean;
  progress?: { done: number; total: number }; error?: { detail: string; kind: string };
}
