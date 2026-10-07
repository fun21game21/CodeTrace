import { createElement } from "react";
import type { EditorProps } from "../../src/editor/MonacoEditor.tsx";
import { lineDecorations } from "../../src/editor/highlight.ts";

export function MonacoEditor({ value, readOnly, highlight }: EditorProps) {
  return createElement("textarea", {
    "aria-label": "Python code",
    value,
    readOnly,
    onChange: () => {},
    "data-read-only": readOnly,
    "data-highlight-line":
      lineDecorations(highlight)[0]?.range.startLineNumber ?? "",
    "data-highlight-mode": highlight?.mode ?? "none",
  });
}
