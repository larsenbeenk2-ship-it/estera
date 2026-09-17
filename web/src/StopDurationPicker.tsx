import {
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import "./StopDurationPicker.css";

type StopDurationPickerProps = {
  value: number;
  onChange: (seconds: number) => void;
  disabled?: boolean;
  label?: string;
};

const ROW_HEIGHT = 40;
const MAX_DURATION = 24 * 60 * 60;

function clamp(value: number, max: number) {
  return Math.min(max, Math.max(0, Math.trunc(Number.isFinite(value) ? value : 0)));
}

function splitDuration(value: number) {
  return [
    Math.floor(value / 3600),
    Math.floor((value % 3600) / 60),
    value % 60,
  ] as const;
}

function DurationWheel({
  label,
  value,
  max,
  onChange,
  disabled,
}: {
  label: string;
  value: number;
  max: number;
  onChange: (value: number) => void;
  disabled: boolean;
}) {
  const id = useId();
  const viewport = useRef<HTMLDivElement>(null);
  const selection = useRef(value);
  const positioned = useRef(false);
  const [displayValue, setDisplayValue] = useState(value);

  // A parent echo of a scroll selection must not reset native momentum.
  // Only a new external value (or the initial mount) moves the viewport.
  useLayoutEffect(() => {
    if (!positioned.current || value !== selection.current) {
      selection.current = value;
      setDisplayValue(value);
      viewport.current?.scrollTo({ top: value * ROW_HEIGHT, behavior: "auto" });
      positioned.current = true;
    }
  }, [value, max]);

  function publish(next: number) {
    if (next === selection.current) return;
    selection.current = next;
    setDisplayValue(next);
    onChange(next);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (disabled) return;
    const steps: Record<string, number> = {
      ArrowUp: -1,
      ArrowDown: 1,
      PageUp: -5,
      PageDown: 5,
    };
    let next: number;
    if (event.key === "Home") next = 0;
    else if (event.key === "End") next = max;
    else if (event.key in steps) next = clamp(selection.current + steps[event.key], max);
    else return;
    event.preventDefault();
    viewport.current?.scrollTo({ top: next * ROW_HEIGHT, behavior: "auto" });
    publish(next);
  }

  return (
    <div className="stop-duration-wheel" data-disabled={disabled || undefined}>
      <span className="stop-duration-unit" id={`${id}-label`}>{label}</span>
      <div className="stop-duration-wheel-frame">
        <div
          ref={viewport}
          className="stop-duration-wheel-scroll"
          role="listbox"
          aria-labelledby={`${id}-label`}
          aria-describedby={`${id}-help`}
          aria-activedescendant={`${id}-option-${displayValue}`}
          aria-disabled={disabled}
          tabIndex={disabled ? -1 : 0}
          onKeyDown={handleKeyDown}
          onScroll={(event) => {
            if (!disabled) publish(clamp(Math.round(event.currentTarget.scrollTop / ROW_HEIGHT), max));
          }}
        >
          {Array.from({ length: max + 1 }, (_, option) => (
            <div
              id={`${id}-option-${option}`}
              className="stop-duration-wheel-option"
              role="option"
              aria-selected={option === displayValue}
              key={option}
              onClick={() => {
                if (disabled) return;
                viewport.current?.focus({ preventScroll: true });
                viewport.current?.scrollTo({
                  top: option * ROW_HEIGHT,
                  behavior: "auto",
                });
                publish(option);
              }}
            >
              {String(option).padStart(2, "0")}
            </div>
          ))}
        </div>
      </div>
      <span id={`${id}-help`} className="stop-duration-sr-only">
        Use the up and down arrow keys to choose {label.toLowerCase()}.
      </span>
    </div>
  );
}

export function StopDurationPicker({
  value,
  onChange,
  disabled = false,
  label = "Stop duration",
}: StopDurationPickerProps) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [typing, setTyping] = useState(false);
  const duration = clamp(value, MAX_DURATION);
  const latestDuration = useRef(duration);
  latestDuration.current = duration;
  const [hours, minutes, seconds] = splitDuration(duration);
  const parts = [hours, minutes, seconds];
  const units = ["Hours", "Minutes", "Seconds"];
  const expanded = open && !disabled;

  function changePart(index: number, next: number) {
    const previous = latestDuration.current;
    const current = [...splitDuration(previous)];
    current[index] = clamp(next, index === 0 ? 24 : current[0] === 24 ? 0 : 59);
    if (current[0] === 24) {
      current[1] = 0;
      current[2] = 0;
    }
    const updated = current[0] * 3600 + current[1] * 60 + current[2];
    latestDuration.current = updated;
    if (updated !== previous) onChange(updated);
  }

  function close() {
    setOpen(false);
    trigger.current?.focus({ preventScroll: true });
  }

  return (
    <div
      className="stop-duration"
      onKeyDown={(event) => {
        if (event.key === "Escape" && expanded) {
          event.preventDefault();
          event.stopPropagation();
          close();
        }
      }}
    >
      <button
        ref={trigger}
        type="button"
        className="stop-duration-trigger"
        aria-expanded={expanded}
        aria-controls={`${id}-panel`}
        aria-label={`${label}: ${hours} hours, ${minutes} minutes, ${seconds} seconds`}
        disabled={disabled}
        onClick={() => setOpen(!expanded)}
      >
        <span className="stop-duration-trigger-copy">
          <span className="stop-duration-label">{label}</span>
          <span className="stop-duration-value" aria-hidden="true">
            <span>{hours}<small>hr</small></span>
            <span>{minutes}<small>min</small></span>
            <span>{seconds}<small>sec</small></span>
          </span>
        </span>
        <svg className="stop-duration-chevron" data-open={expanded || undefined} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>
      {expanded && (
        <div className="stop-duration-panel" id={`${id}-panel`} role="group" aria-label={`Set ${label.toLowerCase()}`}>
          <div className="stop-duration-panel-heading">
            <span>{typing ? "Enter a duration" : "Scroll to set duration"}</span>
            <button type="button" className="stop-duration-mode" aria-pressed={typing} onClick={() => setTyping(!typing)}>
              {typing ? "Use wheels" : "Type values"}
            </button>
          </div>
          <div className={`stop-duration-columns${typing ? " stop-duration-inputs" : ""}`}>
            {units.map((unit, index) => {
              const max = index === 0 ? 24 : hours === 24 ? 0 : 59;
              const locked = index > 0 && hours === 24;
              return typing ? (
                <label className="stop-duration-number-label" key={unit}>
                  <span className="stop-duration-unit">{unit}</span>
                  <input
                    type="number"
                    inputMode="numeric"
                    min={0}
                    max={max}
                    step={1}
                    value={parts[index]}
                    disabled={locked}
                    onChange={(event) => changePart(index, event.target.valueAsNumber)}
                  />
                </label>
              ) : (
                <DurationWheel
                  key={unit}
                  label={unit}
                  value={parts[index]}
                  max={max}
                  disabled={locked}
                  onChange={(next) => changePart(index, next)}
                />
              );
            })}
          </div>
          <div className="stop-duration-panel-footer">
            <span>{hours === 24 ? "24 hours is the maximum." : "Up to 24 hours."}</span>
            <button type="button" className="stop-duration-done" onClick={close}>Done</button>
          </div>
        </div>
      )}
    </div>
  );
}
