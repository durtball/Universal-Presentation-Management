import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PreferencesProvider, usePreferences } from "../state/preferences";

function Controls() {
  const preferences = usePreferences();
  return (
    <>
      <span>
        {preferences.theme}/{preferences.motion}/{preferences.effectiveMotion}
      </span>
      <button onClick={() => preferences.setTheme("classic")}>Classic</button>
      <button onClick={() => preferences.setMotion("reduced")}>Reduced</button>
      <button onClick={() => preferences.setMotion("off")}>Off</button>
    </>
  );
}
test("defaults to Glass and switches themes immediately with persistence", async () => {
  const user = userEvent.setup();
  render(
    <PreferencesProvider>
      <Controls />
    </PreferencesProvider>,
  );
  expect(screen.getByText("glass/full/full")).toBeInTheDocument();
  await user.click(screen.getByText("Classic"));
  expect(document.documentElement).toHaveAttribute("data-theme", "classic");
  expect(localStorage.getItem("upm.theme")).toBe("classic");
});
test("supports Reduced and Off motion", async () => {
  const user = userEvent.setup();
  render(
    <PreferencesProvider>
      <Controls />
    </PreferencesProvider>,
  );
  await user.click(screen.getByText("Reduced"));
  expect(document.documentElement).toHaveAttribute("data-motion", "reduced");
  await user.click(screen.getByText("Off"));
  expect(document.documentElement).toHaveAttribute("data-motion", "off");
});
test("honors prefers-reduced-motion for Full", () => {
  vi.mocked(window.matchMedia).mockReturnValue({
    matches: true,
    media: "",
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  });
  render(
    <PreferencesProvider>
      <Controls />
    </PreferencesProvider>,
  );
  expect(screen.getByText("glass/full/reduced")).toBeInTheDocument();
});
