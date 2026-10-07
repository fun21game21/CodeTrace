import type { SelectedStepView } from "../../trace/index.ts";

export function StepConsole({ view }: { view: SelectedStepView }) {
  return (
    <section
      className="console-panel step-console"
      aria-labelledby="console-title"
    >
      <h2 id="console-title">Console — шаг {view.step}</h2>
      {view.consoleEntries.length === 0 ? (
        <p className="console-empty">На этом шаге вывода ещё нет.</p>
      ) : (
        <pre aria-label="Step Console output">
          {view.consoleEntries.map((entry) => (
            <span
              key={entry.index}
              className={"console-" + entry.kind}
              aria-label={entry.kind === "input" ? "Ввод" : entry.kind}
            >
              {entry.text}
            </span>
          ))}
        </pre>
      )}
    </section>
  );
}
