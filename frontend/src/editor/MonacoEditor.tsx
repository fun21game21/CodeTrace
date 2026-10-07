import { useEffect, useRef } from "react";
import * as monaco from "monaco-editor/editor/editor.api.js";
import "monaco-editor/languages/definitions/python/register.js";
import EditorWorker from "monaco-editor/editor/editor.worker.js?worker";
import type { Highlight } from "../trace/index.ts";
import { lineDecorations } from "./highlight.ts";

(
  self as typeof self & { MonacoEnvironment: monaco.Environment }
).MonacoEnvironment = { getWorker: () => new EditorWorker() };
export interface EditorProps {
  value: string;
  readOnly: boolean;
  onChange(code: string): void;
  highlight?: Highlight | null;
}

export function MonacoEditor({
  value,
  readOnly,
  onChange,
  highlight,
}: EditorProps) {
  const container = useRef<HTMLDivElement>(null);
  const editor = useRef<monaco.editor.IStandaloneCodeEditor | null>(null);
  const change = useRef(onChange);
  const applyingSource = useRef(false);
  const decorations = useRef<monaco.editor.IEditorDecorationsCollection | null>(
    null,
  );
  change.current = onChange;
  useEffect(() => {
    if (!container.current) return;
    const instance = monaco.editor.create(container.current, {
      value: "",
      language: "python",
      theme: "vs",
      lineNumbers: "on",
      automaticLayout: true,
      minimap: { enabled: false },
      fontSize: 14,
      scrollBeyondLastLine: false,
      ariaLabel: "Python code",
      tabSize: 4,
      padding: { top: 16, bottom: 16 },
    });
    editor.current = instance;
    decorations.current = instance.createDecorationsCollection();
    const subscription = instance.onDidChangeModelContent(() => {
      if (!applyingSource.current) change.current(instance.getValue());
    });
    return () => {
      subscription.dispose();
      decorations.current?.clear();
      decorations.current = null;
      instance.getModel()?.dispose();
      instance.dispose();
      editor.current = null;
    };
  }, []);
  useEffect(() => {
    const instance = editor.current;
    if (instance && instance.getValue() !== value) {
      applyingSource.current = true;
      try {
        instance.setValue(value);
      } finally {
        applyingSource.current = false;
      }
    }
  }, [value]);
  useEffect(() => {
    editor.current?.updateOptions({ readOnly });
  }, [readOnly]);
  useEffect(() => {
    decorations.current?.set(lineDecorations(highlight));
    if (highlight?.lineNumber !== null && highlight?.lineNumber !== undefined)
      editor.current?.revealLineInCenterIfOutsideViewport(highlight.lineNumber);
  }, [highlight]);
  return (
    <div className="monaco-host" ref={container} data-read-only={readOnly} />
  );
}
