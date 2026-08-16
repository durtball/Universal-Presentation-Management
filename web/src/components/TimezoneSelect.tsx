import { EVENT_TIMEZONE_OPTIONS } from "../config/timezones";

export function TimezoneSelect({ value, onChange }: {
  value: string;
  onChange: (value: string) => void;
}) {
  const catalogContainsValue = EVENT_TIMEZONE_OPTIONS.some(option => option.value === value);
  const regions = [...new Set(EVENT_TIMEZONE_OPTIONS.map(option => option.region))];

  return <select className="input" required value={value} onChange={event=>onChange(event.target.value)}>
    {!catalogContainsValue && value ? <option value={value}>{value} (existing timezone)</option> : null}
    {regions.map(region=><optgroup label={region} key={region}>
      {EVENT_TIMEZONE_OPTIONS.filter(option=>option.region===region).map(option=><option value={option.value} key={option.value}>{option.label}</option>)}
    </optgroup>)}
  </select>;
}
