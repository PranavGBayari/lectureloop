"use client";
// Join page, built to the Figma frames "LectureLoop / Join · Desktop" and "· Mobile".
import { useRouter } from "next/navigation";
import { useState } from "react";
import { CODE_LENGTH, CodeInput } from "@/components/CodeInput";
import {
  ENTRY_BUTTON,
  ENTRY_ERROR,
  ENTRY_INPUT,
  ENTRY_LABEL,
  EntryHeading,
  EntryShell,
} from "@/components/EntryShell";
import { api, ApiError } from "@/lib/api";
import { studentStore } from "@/lib/studentStore";

export default function JoinPage() {
  const router = useRouter();
  const [code, setCode] = useState("");
  const [nickname, setNickname] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (code.length < CODE_LENGTH) {
      setError(`The lecture code has ${CODE_LENGTH} characters.`);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await api.join(code, nickname.trim());
      studentStore.set(r.session_id, r.student_id);
      router.push(`/s/${r.session_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
      setBusy(false);
    }
  }

  return (
    <EntryShell below={{ href: "/l", label: "I’m the lecturer →" }} step="01 / CONNECT" motto="YOUR PACE. YOUR LOOP.">
      <EntryHeading
        eyebrow="// YOUR NEXT AHA MOMENT"
        title={["Keep up.", "Let it click."]}
        sub={["A quiet companion for the things", "you’re learning right now."]}
      />
      <form onSubmit={onSubmit}>
        <label id="code-label" htmlFor="code" className={`mt-[29px] ${ENTRY_LABEL}`}>
          LECTURE CODE
        </label>
        <CodeInput id="code" labelledBy="code-label" value={code} onChange={setCode} />

        <label htmlFor="nickname" className={`mt-[25px] ${ENTRY_LABEL}`}>
          NICKNAME
        </label>
        <input
          id="nickname"
          value={nickname}
          onChange={(e) => setNickname(e.target.value)}
          maxLength={40}
          autoComplete="nickname"
          placeholder="What should I call you?"
          required
          className={ENTRY_INPUT}
        />

        <button disabled={busy} className={ENTRY_BUTTON}>
          {busy ? "Joining…" : "Join lecture  →"}
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
