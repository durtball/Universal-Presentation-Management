export const PRESENTATION_EXTENSIONS = new Set([".ppt", ".pptx", ".pdf"]);
const configuredConcurrency = Number(import.meta.env.VITE_UPM_UPLOAD_CONCURRENCY ?? 6);
export const DEFAULT_UPLOAD_CONCURRENCY = Number.isInteger(configuredConcurrency)
  ? Math.min(8, Math.max(1, configuredConcurrency))
  : 6;

export interface SelectedUpload {
  id: string;
  file: File;
  relativePath?: string;
  recognized: boolean;
}
export interface SkippedUpload { path: string; reason: string }

let nextUploadId = 0;

export function selectPresentationFiles(files: Iterable<File>): {
  accepted: SelectedUpload[];
  skipped: SkippedUpload[];
} {
  const accepted: SelectedUpload[] = [];
  const skipped: SkippedUpload[] = [];
  for (const file of files) {
    const path = file.webkitRelativePath || file.name;
    const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    let reason = "";
    if (file.name.startsWith("~$")) reason = "Temporary Office lock file";
    else if (file.name === ".DS_Store" || file.name.toLowerCase() === "thumbs.db") reason = "System metadata file";
    if (reason) skipped.push({ path, reason });
    else accepted.push({
      id: `upload-${Date.now()}-${nextUploadId++}`,
      file,
      relativePath: file.webkitRelativePath || undefined,
      recognized: PRESENTATION_EXTENSIONS.has(extension),
    });
  }
  return { accepted, skipped };
}

export async function runBounded<T>(
  values: readonly T[], worker: (value: T) => Promise<void>, concurrency = DEFAULT_UPLOAD_CONCURRENCY,
  beforeEach: () => Promise<void> = async () => undefined,
) {
  let cursor = 0;
  const run = async () => {
    while (cursor < values.length) {
      const value = values[cursor++];
      await beforeEach();
      try {
        await worker(value);
      } catch {
        // A single item owns its failure; it must never terminate the remaining batch.
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(concurrency, values.length) }, run));
}
