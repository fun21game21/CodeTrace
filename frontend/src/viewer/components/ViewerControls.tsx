import { SPEEDS } from "../types.ts";
import type { ViewerController } from "../controller.ts";
import type { ViewerSnapshot } from "../types.ts";

export function ViewerControls({
  controller,
  snapshot: s,
}: {
  controller: ViewerController;
  snapshot: ViewerSnapshot;
}) {
  const ready = s.phase === "READY" && s.committedView !== null;
  const target = s.sliderTarget ?? s.pendingStep ?? s.committedStep ?? 1;
  return (
    <section className="viewer-controls" aria-label="Управление шагами">
      <form
        className="step-form"
        onSubmit={(event) => {
          event.preventDefault();
          controller.commitStepDraft();
        }}
      >
        <label htmlFor="direct-step">Шаг</label>
        <input
          id="direct-step"
          type="text"
          inputMode="numeric"
          autoComplete="off"
          aria-describedby={
            s.stepValidationMessage ? "step-validation" : undefined
          }
          disabled={!ready}
          value={s.directStepDraft}
          onChange={(event) => controller.setStepDraft(event.target.value)}
          onBlur={() => controller.commitStepDraft()}
        />
        <span>/ {s.totalSteps || "—"}</span>
      </form>
      <span className="committed-step" aria-live="polite">
        {s.committedStep === null
          ? "Шаг не выбран"
          : "Шаг " + s.committedStep + " / " + s.totalSteps}
      </span>
      <input
        aria-label="Шаг на шкале"
        type="range"
        min={1}
        max={s.totalSteps || 1}
        step={1}
        value={target}
        disabled={!ready}
        onChange={(event) => controller.sliderInput(Number(event.target.value))}
        onPointerDown={() => controller.pause()}
        onPointerUp={(event) =>
          controller.sliderCommit(Number(event.currentTarget.value))
        }
        onPointerCancel={(event) =>
          controller.sliderCommit(Number(event.currentTarget.value))
        }
        onKeyUp={(event) =>
          controller.sliderCommit(Number(event.currentTarget.value))
        }
        onBlur={(event) =>
          controller.sliderCommit(Number(event.currentTarget.value))
        }
      />
      <div className="play-controls">
        <button
          aria-label="Предыдущий шаг"
          disabled={!ready || s.committedStep === 1}
          onClick={() => controller.previous()}
        >
          ←
        </button>
        <button
          aria-label={s.playing ? "Пауза" : "Воспроизвести"}
          aria-pressed={s.playing}
          disabled={!ready || (!s.playing && s.committedStep === s.totalSteps)}
          onClick={() => (s.playing ? controller.pause() : controller.play())}
        >
          {s.playing ? "Пауза" : "Play"}
        </button>
        <button
          aria-label="Следующий шаг"
          disabled={!ready || s.committedStep === s.totalSteps}
          onClick={() => controller.next()}
        >
          →
        </button>
        <label className="speed-label" htmlFor="play-speed">
          Скорость
          <select
            id="play-speed"
            value={s.speed}
            disabled={!ready}
            onChange={(event) =>
              controller.setSpeed(Number(event.target.value))
            }
          >
            {SPEEDS.map((speed) => (
              <option key={speed} value={speed}>
                {speed} шаг/с
              </option>
            ))}
          </select>
        </label>
      </div>
      {s.pendingStep !== null && (
        <small className="pending-step">Выбирается шаг {s.pendingStep}…</small>
      )}
      {s.stepValidationMessage && (
        <p id="step-validation" className="error" role="alert">
          {s.stepValidationMessage}
        </p>
      )}
      {s.selectionError && (
        <p className="error" role="alert">
          {s.selectionError}
        </p>
      )}
    </section>
  );
}
