import { useState, type FormEvent } from "react";
import type { EventRecord, EventWrite } from "../api/types";
import { DEFAULT_EVENT_TIMEZONE } from "../config/timezones";
import { ErrorSurface } from "./Feedback";
import { TimezoneSelect } from "./TimezoneSelect";

const dateValue = (value?: string) => value?.slice(0, 10) ?? "";

export function EventDialog({ event, save, close }: {
  event?: EventRecord;
  save: (values: EventWrite) => Promise<unknown>;
  close: () => void;
}) {
  const [name, setName] = useState(event?.name ?? "");
  const [startDate, setStartDate] = useState(dateValue(event?.starts_at));
  const [endDate, setEndDate] = useState(dateValue(event?.ends_at));
  const [timezone, setTimezone] = useState(event?.timezone ?? DEFAULT_EVENT_TIMEZONE);
  const [error, setError] = useState<unknown>();
  const [validation, setValidation] = useState("");
  const [saving, setSaving] = useState(false);

  const submit = async (submitEvent: FormEvent) => {
    submitEvent.preventDefault();
    const normalizedName = name.trim();
    if (!normalizedName) { setValidation("Event name is required."); return; }
    if (startDate && endDate && endDate < startDate) {
      setValidation("End Date must be on or after Start Date."); return;
    }
    setValidation(""); setError(undefined); setSaving(true);
    try {
      await save({
        name: normalizedName,
        timezone,
        starts_at: startDate ? `${startDate}T00:00:00Z` : null,
        ends_at: endDate ? `${endDate}T00:00:00Z` : null,
      });
      close();
    } catch (reason) { setError(reason); }
    finally { setSaving(false); }
  };

  return <div className="dialog-backdrop" role="presentation">
    <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="event-dialog-title">
      <header><h2 id="event-dialog-title">{event ? "Edit Event" : "Create Event"}</h2></header>
      <form onSubmit={submit} noValidate>
        <label className="field">Event Name<input className="input" autoFocus required value={name} onChange={e=>setName(e.target.value)} /></label>
        <label className="field">Start Date<input className="input" type="date" value={startDate} onChange={e=>setStartDate(e.target.value)} /></label>
        <label className="field">End Date<input className="input" type="date" min={startDate||undefined} value={endDate} onChange={e=>setEndDate(e.target.value)} /></label>
        <label className="field">Timezone<TimezoneSelect value={timezone} onChange={setTimezone}/></label>
        {validation ? <p className="danger-text" role="alert">{validation}</p> : null}
        {error != null ? <ErrorSurface error={error}/> : null}
        <footer><button className="button" type="button" onClick={close}>Cancel</button><button className="button button--primary" disabled={saving}>{event ? "Save Changes" : "Create"}</button></footer>
      </form>
    </section>
  </div>;
}
