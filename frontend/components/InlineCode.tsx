// Renders `backticked` spans in model-written text (questions, hints, feedback, recaps) as inline
// code. Only complete pairs are converted, so a half-streamed message never flickers into code.
import { Fragment } from "react";

export function InlineCode({ text }: { text: string }) {
  const parts = text.split(/`([^`\n]+)`/);
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <code key={i} className="rounded-[4px] bg-ink px-[0.35em] py-[0.1em] font-code text-[0.88em] text-teal">
            {part}
          </code>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </>
  );
}
