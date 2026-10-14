"use client";

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { ClipboardPaste, FileText, RefreshCw, Upload } from "lucide-react";
import { FormError, StepActions, StepHeader } from "@/components/onboarding/shared";
import { ResumeEditor } from "@/components/resume-editor";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, fetcher, post, put, upload } from "@/lib/api-client";
import type { Resume, ResumeContent } from "@/lib/types";

export const MAX_RESUME_BYTES = 5 * 1024 * 1024;
const ACCEPTED = [".pdf", ".docx"];

/** Step 2: upload (or paste) the resume, then check what we read before going on. */
export function ResumeStep({ onSave, onBack, saving }: {
  onSave: () => Promise<void>;
  onBack: () => void;
  saving: boolean;
}) {
  // 404 until a resume is uploaded: then `master` is undefined and the upload area shows
  const { data: master, mutate, isLoading } = useSWR<Resume>("/resumes/master", fetcher, { shouldRetryOnError: false });
  const [content, setContent] = useState<ResumeContent | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pasting, setPasting] = useState(false);
  const [text, setText] = useState("");
  const [replacing, setReplacing] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (master?.parsed_content && !dirty) setContent(master.parsed_content);
  }, [master, dirty]);

  const afterImport = async () => {
    setDirty(false);
    setReplacing(false);
    setPasting(false);
    setText("");
    await mutate();
  };

  const onFile = async (file: File) => {
    setError(null);
    const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    if (!ACCEPTED.includes(ext)) return setError("Upload a PDF or Word (.docx) file.");
    if (file.size > MAX_RESUME_BYTES) return setError("That file is over 5 MB. Export a smaller PDF and try again.");
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("is_master", "true");
      await upload<Resume>("/resumes/upload", form);
      await afterImport();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const importText = async () => {
    setError(null);
    setBusy(true);
    try {
      await post("/resumes/from-text", { text, is_master: true });
      await afterImport();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      if (master && content && dirty) await put(`/resumes/${master.id}`, { parsed_content: content });
      await onSave();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  const showUpload = !master || replacing;
  return (
    <form onSubmit={submit} noValidate>
      <StepHeader title={showUpload ? "Your resume" : "Check what we read"}
        intro={showUpload ? "Upload your resume as a PDF or Word file (up to 5 MB). We read your education, skills and experience from it."
          : "This is what we read from your resume. Fix anything that's off: every tailored resume and form answer starts from here."}
        why="Your resume is the only source of facts: HireFlow may reorder or reword it for a job, but never adds anything that isn't here." />
      <FormError message={error} />
      <input ref={fileRef} type="file" accept={ACCEPTED.join(",")} className="sr-only" aria-label="Choose your resume file" tabIndex={-1}
        onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])} />

      {isLoading && !replacing ? (
        <div className="space-y-3"><Skeleton className="h-24 w-full rounded-lg" /><Skeleton className="h-48 w-full rounded-lg" /></div>
      ) : showUpload ? (
        <div className="space-y-4">
          <button type="button" disabled={busy}
            onClick={() => fileRef.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files?.[0]; if (f) onFile(f); }}
            className="flex w-full flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed border-primary/40 bg-secondary/60 px-6 py-12 text-center transition-colors hover:border-primary hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60">
            {busy ? <span className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" aria-hidden />
              : <Upload className="h-8 w-8 text-primary" aria-hidden />}
            <span className="text-base font-semibold">{busy ? "Reading your resume…" : "Drop your resume here, or click to choose"}</span>
            <span className="text-xs text-muted-foreground">PDF or DOCX · up to 5 MB</span>
          </button>
          {!pasting ? (
            <Button type="button" variant="ghost" onClick={() => setPasting(true)}><ClipboardPaste /> Paste the text instead</Button>
          ) : (
            <div className="space-y-2">
              <Textarea aria-label="Resume text" rows={10} placeholder="Paste your resume text here" value={text} onChange={(e) => setText(e.target.value)} />
              <div className="flex gap-2">
                <Button type="button" onClick={importText} loading={busy} disabled={text.trim().length < 50}>Read this text</Button>
                <Button type="button" variant="ghost" onClick={() => setPasting(false)}>Cancel</Button>
              </div>
            </div>
          )}
          {replacing && <Button type="button" variant="outline" onClick={() => setReplacing(false)}>Keep my current resume</Button>}
        </div>
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-card px-4 py-3 text-sm">
            <FileText className="h-4 w-4 text-primary" aria-hidden />
            <span className="font-medium">{master?.original_filename || master?.label || "Your resume"}</span>
            <span className="flex-1" />
            <Button type="button" variant="ghost" size="sm" onClick={() => setReplacing(true)}><RefreshCw /> Replace</Button>
          </div>
          {content && <ResumeEditor value={content} onChange={(v) => { setContent(v); setDirty(true); }} />}
        </div>
      )}
      <StepActions onBack={onBack} saving={saving || busy} disabled={showUpload} continueLabel={dirty ? "Save and continue" : "Looks right"} />
    </form>
  );
}
