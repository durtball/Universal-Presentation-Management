import type { EventRecord } from "../api/types";

export function EventPicker({
  events,
  value,
  onChange,
}: {
  events: EventRecord[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="field field--inline">
      <span>Event context</span>
      <select
        className="input"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">Select an event</option>
        {events.map((item) => (
          <option key={item.event_id} value={item.event_id}>
            {item.name}
          </option>
        ))}
      </select>
    </label>
  );
}
