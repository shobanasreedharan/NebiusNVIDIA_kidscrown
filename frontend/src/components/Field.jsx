import { useId } from "react";

// A labelled form control. `as="textarea"` renders a textarea.
export default function Field({ label, hint, as = "input", maxLength, ...props }) {
  const id = useId();
  const countId = as === "textarea" && Number.isInteger(maxLength) ? `${id}-count` : undefined;
  const describedBy = [hint && `${id}-hint`, countId].filter(Boolean).join(" ") || undefined;
  const Control = as;
  const value = typeof props.value === "string" ? props.value : "";
  const formatCount = (number) => new Intl.NumberFormat("en-US").format(number);
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <Control id={id} aria-describedby={describedBy} maxLength={maxLength} {...props} />
      {hint && (
        <p className="hint" id={`${id}-hint`}>
          {hint}
        </p>
      )}
      {countId && (
        <p className="hint field-character-count" id={countId} aria-live="polite">
          {formatCount(value.length)} / {formatCount(maxLength)}
        </p>
      )}
    </div>
  );
}
