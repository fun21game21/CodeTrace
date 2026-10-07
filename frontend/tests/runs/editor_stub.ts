import { createElement } from "react";
import type { EditorProps } from "../../src/editor/MonacoEditor.tsx";
export function MonacoEditor({ value, readOnly }: EditorProps) {
  return createElement("textarea", {
    "aria-label": "Python code",
    "data-read-only": readOnly,
    readOnly,
    value,
    onChange: () => {},
  });
}
