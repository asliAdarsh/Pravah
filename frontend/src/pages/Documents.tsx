import { useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Api, ApiError } from "@/api/client";
import type {
  DocumentDetail,
  DocumentSummary,
  DocumentType,
  EventDetail,
  DocumentsResponse,
  IngestResponse,
} from "@/api/types";
import { useAsyncData } from "@/lib/useAsyncData";
import {
  PAGE_UNAVAILABLE,
  fmtDate,
  fmtNumber,
  fmtPage,
  humanizeEnum,
} from "@/lib/format";
import { useApp } from "@/store/useApp";
import {
  Badge,
  DataTable,
  Disclosure,
  EmptyState,
  KeyValueGrid,
  LineIcon,
  ScreenHeader,
  SectionCard,
  Skeleton,
  Toolbar,
  actionButtonClass,
  controlClass,
  cx,
  ghostButtonClass,
  type DataTableColumn,
} from "@/components/ui";
import { SliceNote, useBounded } from "./_calm";

const DOC_TYPES: DocumentType[] = [
  "DDR",
  "WCR",
  "DGR",
  "INCIDENT_REPORT",
  "WELL_LOG",
  "LESSONS_LEARNED",
];

/**
 * Evidence & Documents screen.
 *
 * Two panes: the register (what exists) and the selected document (what it
 * says). Metadata is reference data, so it sits in a Disclosure; the excerpt,
 * the sections and the linked events stay visible because they are the
 * document's actual content.
 *
 * The ingest block demonstrates the prototype pipeline (OCR → layout →
 * sections → entities → events → depth normalisation → formation mapping) and
 * labels every stage exactly as the API reports it, including its `simulated`
 * flag. It opens from a header button instead of sitting permanently above the
 * register.
 */
export function Documents() {
  const { docId } = useParams();
  const navigate = useNavigate();
  const { currentWellId, wells, openEvidence, notify, meta } = useApp();

  const [wellFilter, setWellFilter] = useState<string>("");
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [ingestOpen, setIngestOpen] = useState(false);

  const listQuery = useAsyncData<DocumentsResponse & { total?: number }>(
    async (signal) => {
      const response = await Api.documents(
        {
          ...(wellFilter ? { well_id: wellFilter } : {}),
          ...(typeFilter ? { doc_type: typeFilter } : {}),
          /* 203 real documents: fetch one page and report the true total, so
             the register can say "showing 25 of 203" instead of quietly
             rendering whatever the API happened to return. */
          limit: 400,
        },
        signal,
      );
      return response;
    },
    [wellFilter, typeFilter],
  );

  const selectedId = docId ?? listQuery.data?.items[0]?.id ?? null;

  const detailQuery = useAsyncData<DocumentDetail | null>(
    async (signal) =>
      selectedId ? Api.document(selectedId, signal) : Promise.resolve(null),
    [selectedId],
  );

  const documents = listQuery.data?.items ?? [];
  const documentTotal = listQuery.data?.total ?? documents.length;
  const register = useBounded(documents, 25, 50);

  const columns: DataTableColumn<DocumentSummary>[] = [
    {
      key: "id",
      header: "Document",
      width: 100,
      render: (row) => (
        <span className="tnum whitespace-nowrap font-semibold text-fg-strong">
          {row.id}
        </span>
      ),
    },
    {
      key: "title",
      header: "Title",
      width: 300,
      render: (row) => (
        <span className="block max-w-[300px]">
          <span className="block truncate text-fg-strong" title={row.title}>
            {row.title}
          </span>
          <span className="tnum block truncate text-2xs text-fg-subtle">
            {`${row.doc_type_label} · ${row.page_count} pp · ${row.event_count} events · ${row.evidence_count} evidence`}
          </span>
        </span>
      ),
    },
    {
      key: "well",
      header: "Well",
      width: 76,
      render: (row) => (
        <span className="whitespace-nowrap text-fg">
          {row.well_name ?? "Unattached"}
        </span>
      ),
    },
    {
      key: "date",
      header: "Dated",
      width: 96,
      render: (row) => (
        <span className="tnum whitespace-nowrap">{fmtDate(row.doc_date)}</span>
      ),
    },
    {
      key: "evidence",
      header: "Evidence",
      width: 92,
      align: "right",
      render: (row) => <span className="tnum">{row.evidence_count}</span>,
    },
  ];

  return (
    <div className="flex flex-col gap-3">
      <ScreenHeader
        title="Evidence &amp; documents"
        subtitle="The corpus the evidence chain is drawn from. Pick a document to read its stored excerpt, sections and the events that cite it."
        meta={
          <>
            <Badge tone="warning">
              {meta?.dataset_label ?? "Real public data"}
            </Badge>
            <Badge tone="neutral">
              {`${register.shown} of ${documentTotal} documents`}
            </Badge>
          </>
        }
        actions={
          <button
            type="button"
            onClick={() => setIngestOpen((value) => !value)}
            aria-expanded={ingestOpen}
            className={ingestOpen ? ghostButtonClass : actionButtonClass}
          >
            <LineIcon name="document" size={12} />{" "}
            {ingestOpen ? "CLOSE INGEST" : "INGEST DOCUMENT"}
          </button>
        }
      />

      {listQuery.error && (
        <ErrorBanner message={listQuery.error} onRetry={listQuery.reload} />
      )}

      {ingestOpen && (
        <IngestPanel
          defaultWellId={wellFilter || currentWellId}
          wells={wells.map((well) => ({ id: well.id, name: well.name }))}
          onIngested={(response) => {
            notify(
              `Ingested ${response.document.id} · ${response.extracted_events.length} event(s) extracted`,
              "success",
            );
            listQuery.reload();
            navigate(`/documents/${response.document.id}`);
          }}
        />
      )}

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <SectionCard
          title="Document register"
          description="Every record carries its own provenance; operator-supplied documents are not simulated."
        >
          <Toolbar className="-mx-3 -mt-2 mb-1 flex flex-wrap items-center gap-2 rounded-t-md border-b">
            <label className="flex items-center gap-1.5 text-2xs uppercase tracking-[0.06em] text-fg-muted">
              Well
              <select
                value={wellFilter}
                onChange={(event) => setWellFilter(event.target.value)}
                className={cx(controlClass, "w-44")}
              >
                <option value="">All wells</option>
                {wells.map((well) => (
                  <option key={well.id} value={well.id}>
                    {well.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-1.5 text-2xs uppercase tracking-[0.06em] text-fg-muted">
              Type
              <select
                value={typeFilter}
                onChange={(event) => setTypeFilter(event.target.value)}
                className={cx(controlClass, "w-40")}
              >
                <option value="">All types</option>
                {DOC_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {humanizeEnum(type)}
                  </option>
                ))}
              </select>
            </label>
            <span className="tnum ml-auto text-2xs text-fg-muted">
              {listQuery.loading
                ? "Loading…"
                : `${register.shown} of ${documentTotal}`}
            </span>
          </Toolbar>

          {listQuery.loading && documents.length === 0 ? (
            <div className="space-y-2 p-3">
              <Skeleton className="h-3 w-full" />
              <Skeleton className="h-3 w-11/12" />
              <Skeleton className="h-3 w-9/12" />
            </div>
          ) : (
            <DataTable
              className="scroll-thin max-h-[calc(100vh-22rem)] overflow-y-auto"
              columns={columns}
              rows={register.visible}
              rowKey={(row) => row.id}
              selectedKey={selectedId ?? undefined}
              onRowClick={(row) => navigate(`/documents/${row.id}`)}
              empty={
                <EmptyState
                  title="No documents match the filter"
                  hint="Clear the well or type filter, or ingest an operator-supplied document from the header button."
                />
              }
            />
          )}
          <SliceNote
            className="mt-2"
            shown={register.shown}
            total={documentTotal}
            noun="documents"
            onMore={register.more}
          />
        </SectionCard>

        <DocumentDetailPanel
          detail={detailQuery.data ?? null}
          loading={detailQuery.loading}
          error={detailQuery.error}
          onRetry={detailQuery.reload}
          onOpenEvidence={openEvidence}
        />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Detail
 * ------------------------------------------------------------------ */

function DocumentDetailPanel({
  detail,
  loading,
  error,
  onRetry,
  onOpenEvidence,
}: {
  detail: DocumentDetail | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onOpenEvidence: (eventId: string) => void;
}) {
  const linkedEvents = useAsyncData<EventDetail[]>(
    async (signal) => {
      if (!detail?.well_id) return [];
      const response = await Api.events(
        { well_id: detail.well_id, limit: 200 },
        signal,
      );
      return response.items.filter((event) => event.document?.id === detail.id);
    },
    [detail?.id, detail?.well_id],
  );
  const linked = useBounded(linkedEvents.data ?? [], 10, 20);
  const sections = useBounded(detail?.sections ?? [], 6, 12);
  if (error) {
    return (
      <SectionCard title="Document">
        <ErrorBanner message={error} onRetry={onRetry} />
      </SectionCard>
    );
  }

  if (loading && !detail) {
    return (
      <SectionCard title="Document">
        <div className="space-y-2 p-3">
          <Skeleton className="h-3 w-2/3" />
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      </SectionCard>
    );
  }

  if (!detail) {
    return (
      <SectionCard title="Document">
        <EmptyState
          title="Select a document"
          hint="Pick a row in the register to read its sections."
        />
      </SectionCard>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <SectionCard
        title={detail.title}
        description={`${detail.id} · ${detail.filename}`}
        actions={
          <Badge tone={detail.is_simulated ? "warning" : "muted"}>
            {detail.is_simulated ? "Simulated" : "Operator supplied"}
          </Badge>
        }
        dense
      >
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone="info">{detail.doc_type_label}</Badge>
          <Badge tone="neutral">{detail.well_name ?? "Unattached"}</Badge>
          <Badge tone="muted">{fmtDate(detail.doc_date)}</Badge>
          <Badge tone="neutral">
            {`${detail.event_count} events · ${detail.evidence_count} evidence rows`}
          </Badge>
        </div>

        <div className="mt-2">
          <Disclosure
            summary="Document metadata"
            badge={<Badge tone="muted">reference</Badge>}
          >
            <KeyValueGrid
              columns={2}
              items={[
                { label: "Type", value: detail.doc_type_label },
                { label: "Well", value: detail.well_name ?? "Unattached" },
                { label: "Dated", value: fmtDate(detail.doc_date), mono: true },
                { label: "Pages", value: String(detail.page_count), mono: true },
                { label: "Source system", value: detail.source_system, mono: true },
                { label: "OCR engine", value: detail.ocr_engine, mono: true },
                { label: "Extraction", value: detail.extraction_method, mono: true },
                { label: "Events", value: fmtNumber(detail.event_count), mono: true },
                {
                  label: "Evidence rows",
                  value: fmtNumber(detail.evidence_count),
                  mono: true,
                },
                { label: "Provenance", value: detail.data_provenance, mono: true },
                {
                  label: "Origin",
                  value: detail.is_simulated ? "Simulated" : "Operator supplied",
                },
                {
                  label: "Page numbers",
                  value: detail.page_count > 0 ? "supplied by the API" : PAGE_UNAVAILABLE,
                },
              ]}
            />
          </Disclosure>
        </div>
      </SectionCard>

      <SectionCard title={`Stored excerpt (${detail.excerpt.length} chars)`} dense>
        <p className="inset-surface scroll-thin max-h-56 overflow-y-auto px-2.5 py-2 text-xs leading-relaxed text-fg">
          {detail.excerpt ||
            "No excerpt stored for this document in the record."}
        </p>
      </SectionCard>

      <SectionCard title={`Sections (${detail.sections.length})`} dense>
        {detail.sections.length === 0 ? (
          <p className="py-1 text-xs text-fg-muted">
            No sections were extracted from this document.
          </p>
        ) : (
          <ul className="scroll-thin max-h-72 divide-y divide-line overflow-y-auto">
            {sections.visible.map((section, index) => (
              <li key={`${section.heading}-${index}`} className="py-1.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-xs font-semibold text-fg-strong">
                    {section.heading}
                  </span>
                  <Badge tone={section.page === null ? "warning" : "muted"}>
                    {fmtPage(section.page)}
                  </Badge>
                </div>
                <p className="mt-0.5 max-h-24 overflow-y-auto text-2xs leading-snug text-fg-muted">
                  {section.text}
                </p>
              </li>
            ))}
          </ul>
        )}
        <SliceNote
          className="mt-2"
          shown={sections.shown}
          total={sections.total}
          noun="sections"
          onMore={sections.more}
        />
      </SectionCard>

      <SectionCard
        title="Linked events"
        description="Events whose evidence chain cites this document. Open one to read its audit chain."
        dense
        actions={
          <span className="text-2xs text-fg-muted">
            {detail.event_count} linked · {detail.evidence_count} evidence rows
          </span>
        }
      >
        {linkedEvents.loading && (
          <div className="space-y-1.5 py-1">
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-4/5" />
          </div>
        )}
        {!linkedEvents.loading && linked.total === 0 && (
          <p className="py-1 text-2xs leading-snug text-fg-muted">
            {detail.event_count === 0
              ? "No event in the record cites this document."
              : "The register reports linked events, but /events returned none for this well — open an event from the Events screens to read its audit chain."}
          </p>
        )}
        <ul className="divide-y divide-line">
          {linked.visible.map((event) => (
            <li
              key={event.id}
              className="flex items-center justify-between gap-2 py-1.5"
            >
              <span className="min-w-0">
                <span className="block truncate text-xs font-semibold text-fg-strong">
                  {event.event_label}
                </span>
                <span className="tnum block text-2xs text-fg-muted">
                  {event.id} · MD {fmtNumber(event.md)} m · TVD{" "}
                  {fmtNumber(event.tvd)} m · {event.formation}
                </span>
              </span>
              <span className="flex shrink-0 items-center gap-1.5">
                <Badge tone="muted">{event.evidence_count} evidence</Badge>
                <button
                  type="button"
                  onClick={() => onOpenEvidence(event.id)}
                  className="text-2xs font-semibold text-accent underline underline-offset-2"
                >
                  [AUDIT CHAIN]
                </button>
              </span>
            </li>
          ))}
        </ul>
        <SliceNote
          className="mt-2"
          shown={linked.shown}
          total={linked.total}
          noun="linked events"
          onMore={linked.more}
        />
      </SectionCard>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Ingest
 * ------------------------------------------------------------------ */

function IngestPanel({
  defaultWellId,
  wells,
  onIngested,
}: {
  defaultWellId: string;
  wells: { id: string; name: string }[];
  onIngested: (response: IngestResponse) => void;
}) {
  const { notify } = useApp();
  const [wellId, setWellId] = useState(defaultWellId);
  const [docType, setDocType] = useState<DocumentType>("DDR");
  const [filename, setFilename] = useState("");
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [result, setResult] = useState<IngestResponse | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (text.trim().length === 0) {
      setFormError(
        "Paste or upload the document text — the extraction pipeline runs on text, not scans.",
      );
      return;
    }
    setFormError(null);
    setSubmitting(true);
    try {
      const response = await Api.ingest({
        filename: filename.trim() || `OPERATOR-${Date.now()}.txt`,
        well_id: wellId === "" ? null : wellId,
        doc_type: docType,
        doc_date: new Date().toISOString().slice(0, 10),
        text,
        ocr_engine: "NONE",
      });
      setResult(response);
      onIngested(response);
    } catch (cause) {
      const message =
        cause instanceof ApiError ? cause.message : "Ingestion request failed";
      setFormError(message);
      notify(message, "critical");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <SectionCard
      title="Operator document ingest"
      description="Runs the extraction pipeline and stores whatever it extracts. Nothing is verified."
    >
      <form
        onSubmit={submit}
        className="grid grid-cols-1 gap-2 lg:grid-cols-[220px_minmax(0,1fr)]"
      >
        <div className="flex flex-col gap-2">
          <label className="flex flex-col gap-1 text-2xs uppercase tracking-[0.06em] text-fg-muted">
            Attach to well
            <select
              value={wellId}
              onChange={(event) => setWellId(event.target.value)}
              className={controlClass}
            >
              <option value="">Unattached</option>
              {wells.map((well) => (
                <option key={well.id} value={well.id}>
                  {well.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-2xs uppercase tracking-[0.06em] text-fg-muted">
            Document type
            <select
              value={docType}
              onChange={(event) =>
                setDocType(event.target.value as DocumentType)
              }
              className={controlClass}
            >
              {DOC_TYPES.map((type) => (
                <option key={type} value={type}>
                  {humanizeEnum(type)}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-2xs uppercase tracking-[0.06em] text-fg-muted">
            Filename
            <input
              value={filename}
              onChange={(event) => setFilename(event.target.value)}
              placeholder="DDR-2024-118.txt"
              className={controlClass}
            />
          </label>
          <label className="flex flex-col gap-1 text-2xs uppercase tracking-[0.06em] text-fg-muted">
            Or choose a text file
            <input
              type="file"
              accept=".txt,text/plain"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (!file) return;
                setFilename(file.name);
                void file.text().then((content) => setText(content));
              }}
              className="text-2xs text-fg-muted file:mr-2 file:rounded-sm file:border file:border-line-strong file:bg-surface-1 file:px-2 file:py-0.5 file:text-2xs"
            />
          </label>
          <button
            type="submit"
            disabled={submitting}
            className={actionButtonClass}
          >
            {submitting ? "RUNNING PIPELINE…" : "[RUN INGEST]"}
          </button>
          {formError && <p className="text-2xs text-crit">{formError}</p>}
        </div>

        <label className="flex flex-col gap-1 text-2xs uppercase tracking-[0.06em] text-fg-muted">
          Document text
          <textarea
            value={text}
            onChange={(event) => setText(event.target.value)}
            rows={8}
            spellCheck={false}
            placeholder={
              "Day 12 – Drilling Summary\nCirculated at 1500 m MD. Mud loss of 12 bbl/hr observed while drilling ahead…"
            }
            className="scroll-thin inset-surface p-2 font-mono text-xs text-fg"
          />
        </label>
      </form>

      {result && <IngestResult result={result} />}
    </SectionCard>
  );
}

function IngestResult({ result }: { result: IngestResponse }) {
  return (
    <div className="mt-3 border-t border-line pt-2">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-[0.06em] text-fg-strong">
          Pipeline result
        </h3>
        <span className="text-2xs text-fg-muted">
          {`Stored as ${result.document.id} · ${result.data_provenance}`}
        </span>
        <Badge tone="warning">Unverified operator input</Badge>
      </div>

      <ol className="mt-2 grid grid-cols-1 gap-1 sm:grid-cols-2 lg:grid-cols-4">
        {result.pipeline.map((stage) => (
          <li
            key={stage.stage}
            className={cx(
              "inset-surface px-2 py-1.5",
              stage.status === "FAILED" && "border-crit-line bg-crit-soft",
            )}
          >
            <div className="flex items-center justify-between gap-1.5">
              <span className="text-2xs font-semibold uppercase tracking-[0.06em] text-fg-strong">
                {stage.stage}
              </span>
              <Badge
                tone={
                  stage.status === "OK"
                    ? "success"
                    : stage.status === "FAILED"
                      ? "critical"
                      : "warning"
                }
              >
                {stage.status}
              </Badge>
            </div>
            <p className="mt-0.5 text-2xs leading-snug text-fg-muted">
              {stage.detail}
            </p>
            {stage.simulated && (
              <p className="mt-0.5 text-2xs font-semibold uppercase tracking-[0.06em] text-warn">
                Simulated stage
              </p>
            )}
          </li>
        ))}
      </ol>

      {result.warnings.length > 0 && (
        <ul className="mt-2 space-y-0.5 rounded-sm border border-warn-line bg-warn-soft px-2 py-1.5">
          {result.warnings.map((warning) => (
            <li key={warning} className="text-2xs text-warn">
              {warning}
            </li>
          ))}
        </ul>
      )}

      <div className="mt-2 grid grid-cols-1 gap-2 lg:grid-cols-2">
        <SectionCard
          title={`Extracted events (${result.extracted_events.length})`}
          dense
        >
          {result.extracted_events.length === 0 ? (
            <p className="py-1 text-xs text-fg-muted">
              No drilling events were extracted from this text. Nothing was
              invented to fill the gap.
            </p>
          ) : (
            <ul className="divide-y divide-line">
              {result.extracted_events.map((event) => (
                <li key={event.id} className="py-1.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="tnum text-2xs text-fg-muted">
                      {event.id}
                    </span>
                    <span className="text-xs font-semibold text-fg-strong">
                      {event.event_label}
                    </span>
                    <Badge tone="muted">{humanizeEnum(event.severity)}</Badge>
                  </div>
                  <p className="mt-0.5 text-2xs text-fg-muted">
                    MD {event.md} m · TVD {event.tvd} m · {event.formation} ·{" "}
                    {event.evidence_count} evidence
                  </p>
                </li>
              ))}
            </ul>
          )}
        </SectionCard>

        <SectionCard title={`Extracted sections (${result.sections.length})`} dense>
          {result.sections.length === 0 ? (
            <p className="py-1 text-xs text-fg-muted">
              No sections were segmented from this text.
            </p>
          ) : (
            <ul className="divide-y divide-line">
              {result.sections.map((section, index) => (
                <li
                  key={`${section.heading}-${index}`}
                  className="flex items-center justify-between gap-2 py-1.5"
                >
                  <span className="text-xs font-semibold text-fg-strong">
                    {section.heading}
                  </span>
                  <Badge tone={section.page === null ? "warning" : "muted"}>
                    {fmtPage(section.page)}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </SectionCard>
      </div>
    </div>
  );
}

function ErrorBanner({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="flex items-start gap-2 rounded-sm border border-crit-line bg-crit-soft px-3 py-2">
      <LineIcon
        name="warning"
        size={14}
        className="mt-px shrink-0 text-crit"
      />
      <div className="min-w-0 flex-1">
        <p className="text-xs font-semibold text-crit">Request failed</p>
        <p className="text-2xs text-crit">{message}</p>
      </div>
      <button
        type="button"
        onClick={onRetry}
        className="shrink-0 rounded-sm border border-crit px-2 py-1 text-2xs font-semibold uppercase text-crit"
      >
        Retry
      </button>
    </div>
  );
}
