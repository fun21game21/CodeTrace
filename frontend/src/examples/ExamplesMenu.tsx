import { EXAMPLES } from "./catalog.ts";
export function ExamplesMenu({
  disabled,
  onSelect,
}: {
  disabled: boolean;
  onSelect: (id: string) => void;
}) {
  return (
    <label className="examples-menu">
      Примеры
      <select
        aria-label="Примеры"
        value=""
        disabled={disabled}
        onChange={(event) => onSelect(event.target.value)}
      >
        <option value="" disabled>
          Выбрать…
        </option>
        {EXAMPLES.map((example) => (
          <option key={example.id} value={example.id}>
            {example.title}
          </option>
        ))}
      </select>
    </label>
  );
}
