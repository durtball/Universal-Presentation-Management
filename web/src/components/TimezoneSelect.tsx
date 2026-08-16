import { EVENT_TIMEZONE_OPTIONS } from "../config/timezones";
import { ThemedSelect, type ThemedSelectOption } from "./ThemedSelect";

export function TimezoneSelect({ value, onChange }: {
  value: string;
  onChange: (value: string) => void;
}) {
  const options: ThemedSelectOption[] = EVENT_TIMEZONE_OPTIONS.map(option=>({
    label: option.label, value: option.value, group: option.region,
  }));
  if (value && !options.some(option=>option.value===value)) {
    options.unshift({label:`${value} (existing timezone)`,value,group:"Existing timezone"});
  }

  return <ThemedSelect value={value} options={options} onChange={onChange}/>;
}
