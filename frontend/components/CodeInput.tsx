"use client";
// Six-box lecture code field (Figma "Code character 1-6"). Each box is a real input with its own
// label ("Code character 1 of 6"), so screen readers announce where you are. Typing moves forward,
// Backspace moves back, the arrow keys move between boxes, and a pasted code fills them all.
import { useRef } from "react";

export const CODE_LENGTH = 6;
const clean = (s: string) => s.toUpperCase().replace(/[^A-Z0-9]/g, "");

export function CodeInput({
  value,
  onChange,
  id,
  labelledBy,
}: {
  value: string;
  onChange: (v: string) => void;
  id: string; // id of the first box, so a <label htmlFor> focuses it
  labelledBy?: string;
}) {
  const boxes = useRef<(HTMLInputElement | null)[]>([]);
  // The latest value, updated synchronously: focus moves before React re-renders, and the next box's
  // focus handler must already see the character that was just typed.
  const current = useRef(value);
  current.current = value;
  const emit = (next: string) => {
    current.current = next;
    onChange(next);
  };
  const focus = (i: number) => boxes.current[Math.max(0, Math.min(CODE_LENGTH - 1, i))]?.focus();

  /** Insert `text` starting at box i (typing one character, or pasting several). */
  function insert(i: number, text: string) {
    const chars = clean(text);
    if (!chars) return;
    const v = current.current;
    const next = (v.slice(0, i) + chars + v.slice(i + chars.length)).slice(0, CODE_LENGTH);
    emit(next);
    focus(i + chars.length);
  }

  function onKeyDown(i: number, e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Backspace") {
      e.preventDefault();
      // Clear this box if it has a character, otherwise step back and clear the previous one.
      const v = current.current;
      const at = v[i] ? i : i - 1;
      if (at >= 0) emit(v.slice(0, at) + v.slice(at + 1));
      focus(at);
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      focus(i - 1);
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      focus(Math.min(i + 1, current.current.length));
    }
  }

  return (
    <div role="group" aria-labelledby={labelledBy} className="mt-[11px] flex gap-2 sm:gap-[10px]">
      {Array.from({ length: CODE_LENGTH }, (_, i) => (
        <input
          key={i}
          ref={(el) => {
            boxes.current[i] = el;
          }}
          id={i === 0 ? id : undefined}
          aria-label={`Code character ${i + 1} of ${CODE_LENGTH}`}
          value={value[i] ?? ""}
          onChange={(e) => {
            const typed = e.target.value;
            const v = current.current;
            if (typed === "") emit(v.slice(0, i) + v.slice(i + 1)); // Delete key or cut
            else insert(i, typed.replace(v[i] ?? "", "") || typed); // the new character, not the old one
          }}
          onKeyDown={(e) => onKeyDown(i, e)}
          onPaste={(e) => {
            e.preventDefault();
            insert(0, e.clipboardData.getData("text")); // a pasted code always fills from the first box
          }}
          // Clicking any empty box puts you at the first empty one, so the code has no gaps.
          onFocus={(e) => {
            if (i > current.current.length) focus(current.current.length);
            else e.target.select();
          }}
          inputMode="text"
          autoCapitalize="characters"
          autoComplete="off"
          autoCorrect="off"
          spellCheck={false}
          maxLength={CODE_LENGTH}
          placeholder="–"
          className="h-14 w-[43px] rounded-[8px] border border-line bg-ink text-center font-code text-[21px] text-fg caret-teal outline-none transition-colors placeholder:text-line focus:border-teal focus:placeholder:text-transparent sm:w-[55px]"
        />
      ))}
    </div>
  );
}
