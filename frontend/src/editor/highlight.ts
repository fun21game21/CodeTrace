import type { Highlight } from "../trace/index.ts";

/** Pure plan consumed by Monaco and the test adapter; never edits source. */
export function lineDecorations(highlight: Highlight | null | undefined) {
  if (!highlight || highlight.lineNumber === null || highlight.mode === "none")
    return [];
  const line = highlight.lineNumber;
  return [
    {
      range: {
        startLineNumber: line,
        endLineNumber: line,
        startColumn: 1,
        endColumn: 1,
      },
      options: {
        isWholeLine: true,
        className: "ct-line-" + highlight.mode,
        linesDecorationsClassName: "ct-gutter-" + highlight.mode,
        description: "CodeTrace " + highlight.mode,
      },
    },
  ];
}
