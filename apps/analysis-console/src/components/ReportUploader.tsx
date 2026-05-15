import { useRef, useState } from 'react';
import { parseReportJson } from '../lib/reportParser';
import type { LoadedReport } from '../types/reports';
import { Button } from './ui';

export function ReportUploader({ onLoad }: { onLoad: (report: LoadedReport) => void }) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleFile(file?: File) {
    if (!file) return;
    setError(null);
    try {
      const json = JSON.parse(await file.text());
      onLoad(parseReportJson(json, file.name));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not parse JSON report.');
    }
  }

  return (
    <div className="uploader" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); void handleFile(event.dataTransfer.files[0]); }}>
      <input ref={inputRef} type="file" accept="application/json,.json" hidden onChange={(event) => void handleFile(event.target.files?.[0])} />
      <div>
        <strong>Import report JSON</strong>
        <p>Drop a production component, threaded load, or Tri-Match context report.</p>
      </div>
      <Button onClick={() => inputRef.current?.click()}>Choose JSON</Button>
      {error ? <p className="error">{error}</p> : null}
    </div>
  );
}
