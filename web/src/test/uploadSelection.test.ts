import { describe, expect, it } from "vitest";
import { runBounded, selectPresentationFiles } from "../components/uploadSelection";

function file(name: string, path = "", type = "application/octet-stream") {
  const value = new File(["content"], name, { type });
  Object.defineProperty(value, "webkitRelativePath", { value: path });
  return value;
}

describe("presentation upload selection", () => {
  it.each(["Deck.pptx", "DECK.PPTX", "slides.Pdf", "presentation.PPT", "deck with spaces.pptx", "基調講演.pptx"])(
    "accepts one supported file named %s regardless of MIME", (name) => {
      const result = selectPresentationFiles([file(name)]);
      expect(result.accepted.map((item) => item.file.name)).toEqual([name]);
      expect(result.skipped).toEqual([]);
    },
  );

  it("retains supported and unknown nested files while skipping only incidentals", () => {
    const result = selectPresentationFiles([
      file("a.pptx", "root/day1/room1/a.pptx"), file("b.pdf", "root/day1/room1/b.pdf"),
      file("c.PPTX", "root/day1/room2/c.PPTX"), file("slides.ppt", "root/day2/room1/slides.ppt"),
      file("notes.txt", "root/day2/notes.txt"), file(".DS_Store", "root/.DS_Store"),
      file("~$temporary.pptx", "root/day2/~$temporary.pptx"),
    ]);
    expect(result.accepted.map((item) => item.relativePath)).toEqual([
      "root/day1/room1/a.pptx", "root/day1/room1/b.pdf", "root/day1/room2/c.PPTX", "root/day2/room1/slides.ppt",
      "root/day2/notes.txt",
    ]);
    expect(result.skipped).toHaveLength(2);
    expect(result.accepted.find((item) => item.file.name === "notes.txt")?.recognized).toBe(false);
  });

  it("does not collapse duplicate basenames from different folders", () => {
    const result = selectPresentationFiles([file("deck.pptx", "root/room-a/deck.pptx"), file("deck.pptx", "root/room-b/deck.pptx")]);
    expect(result.accepted).toHaveLength(2);
    expect(new Set(result.accepted.map((item) => item.id)).size).toBe(2);
  });

  it("bounds concurrency and continues after an individual failure", async () => {
    let active = 0; let maximum = 0; const completed: string[] = [];
    await runBounded(["a", "b", "c"], async (value) => {
      active += 1; maximum = Math.max(maximum, active);
      try { if (value === "b") throw new Error("failed"); completed.push(value); }
      catch { /* the queue item owns its failure, as the real upload worker does */ }
      finally { active -= 1; }
    }, 2);
    expect(maximum).toBeLessThanOrEqual(2);
    expect(completed).toEqual(["a", "c"]);
  });

  it("drains a 1,000-item batch with bounded concurrency", async () => {
    const selected = [
      ...Array.from({ length: 950 }, (_, index) => file(`${index}.pptx`, `root/room/${index}.pptx`)),
      ...Array.from({ length: 50 }, (_, index) => file(`material-${index}.xyz`, `root/source/material-${index}.xyz`)),
      ...Array.from({ length: 20 }, (_, index) => file(`~$lock-${index}.pptx`, `root/~$lock-${index}.pptx`)),
    ];
    const result = selectPresentationFiles(selected);
    expect(result.accepted).toHaveLength(1_000);
    expect(result.accepted.filter((item) => !item.recognized)).toHaveLength(50);
    expect(result.skipped).toHaveLength(20);
    let active = 0; let maximum = 0; let completed = 0;
    await runBounded(result.accepted, async () => {
      active += 1; maximum = Math.max(maximum, active);
      await Promise.resolve(); completed += 1; active -= 1;
    });
    expect(maximum).toBe(6);
    expect(completed).toBe(1_000);
  });
});
