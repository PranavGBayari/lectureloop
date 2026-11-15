"use client";
// Create-session page. The Figma file has no frame for it, so it reuses the Join frame's shell,
// card and controls with lecturer wording.
import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  ENTRY_BUTTON,
  ENTRY_ERROR,
  ENTRY_INPUT,
  ENTRY_LABEL,
  EntryHeading,
  EntryShell,
} from "@/components/EntryShell";
import { api, ApiError } from "@/lib/api";

export default function NewSessionPage() {
  const router = useRouter();
  const [title, setTitle] = useState("Functors and Applicatives");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.createSession(title.trim());
      router.push(`/l/${r.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
      setBusy(false);
    }
  }

  return (
    <EntryShell below={{ href: "/", label: "← I’m a student" }} step="01 / CREATE" motto="SIGNAL, NOT SURVEILLANCE.">
      <EntryHeading
        eyebrow="// FOR LECTURERS"
        title={["Teach once.", "Hear it land."]}
        sub={["Start a session and share the code.", "You see the room, never the individual."]}
      />
      <form onSubmit={onSubmit}>
        <label htmlFor="title" className={`mt-[29px] ${ENTRY_LABEL}`}>
          LECTURE TITLE
        </label>
        <input
          id="title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={200}
          placeholder="What are you teaching today?"
          required
          className={ENTRY_INPUT}
        />
        <p className="mt-4 text-[13px] text-muted">
          You’ll get a six-character join code to put on the projector. Students need no account.
        </p>
        <button disabled={busy || !title.trim()} className={ENTRY_BUTTON}>
          {busy ? "Creating…" : "Start session  →"}
        </button>
        {error && (
          <p role="alert" className={ENTRY_ERROR}>
            {error}
          </p>
        )}
      </form>
    </EntryShell>
  );
}
