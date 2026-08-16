export const PRESENTATION_EXTENSIONS = new Set([".ppt", ".pptx", ".pdf"]);
export const DEFAULT_UPLOAD_CONCURRENCY = 3;

export interface SelectedUpload {
  id: string;
  file: File;
  relativePath?: string;
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
    else if (!PRESENTATION_EXTENSIONS.has(extension)) reason = "Unsupported file type";
    if (reason) skipped.push({ path, reason });
    else accepted.push({
      id: `upload-${Date.now()}-${nextUploadId++}`,
      file,
      relativePath: file.webkitRelativePath || undefined,
    });
  }
  return { accepted, skipped };
}

export async function runBounded<T>(
  values: readonly T[], worker: (value: T) => Promise<void>, concurrency = DEFAULT_UPLOAD_CONCURRENCY,
) {
  let cursor = 0;
  const run = async () => {
    while (cursor < values.length) {
      const value = values[cursor++];
      await worker(value);
    }
  };
  await Promise.all(Array.from({ length: Math.min(concurrency, values.length) }, run));
}
