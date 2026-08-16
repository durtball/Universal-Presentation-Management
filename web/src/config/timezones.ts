export interface TimezoneOption {
  label: string;
  value: string;
  region: string;
}

export const DEFAULT_EVENT_TIMEZONE = "America/Chicago";

export const EVENT_TIMEZONE_OPTIONS: readonly TimezoneOption[] = [
  { label: "Eastern Time", value: "America/New_York", region: "North America" },
  { label: "Central Time", value: "America/Chicago", region: "North America" },
  { label: "Mountain Time", value: "America/Denver", region: "North America" },
  { label: "Mountain Time — Arizona", value: "America/Phoenix", region: "North America" },
  { label: "Pacific Time", value: "America/Los_Angeles", region: "North America" },
  { label: "Alaska Time", value: "America/Anchorage", region: "North America" },
  { label: "Hawaii Time", value: "Pacific/Honolulu", region: "North America" },
  { label: "Atlantic Time", value: "America/Halifax", region: "North America" },
  { label: "Newfoundland Time", value: "America/St_Johns", region: "North America" },
];
