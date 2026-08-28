# ADR-0018: Row Presentation Entries and Rotating-Slide Inheritance

- **Status:** Accepted
- **Date:** 2026-08-28

## Decision

A meaningful structured-program row creates an independent expected `Presentation` and upload slot unless the source supplies an explicit authoritative presentation grouping key. Rows may share one `Session`; `SessionParticipant` records describe Session participation while `PresentationPresenter` records identify the presenter(s) for a specific upload target. Similar titles, rooms, dates, and times never merge Presentation identity.

Rotating slides are separate operational assignments and never contribute to expected/ready/missing presenter-Presentation counts. Assignments use relational event/day, optional room, optional Session, presentation-version identity, authority, active state, and revision. Effective resolution is Session override, then room/day override, then event/day global. Site-local overrides outrank Central defaults at the same scope and survive ordinary Central snapshot convergence until explicitly cleared.

Central deployment snapshots carry every Presentation/link and Central rotation default. Site retains the same UUID identities and independently retains Site-local rotation overrides. Media assignment always targets a Presentation; presenter search is discovery, not attachment to a Person.

## Consequences

Import-row identity derived from the source digest, worksheet, and source row makes default materialization idempotent. Explicit external presentation identity intentionally groups rows. Repair of legacy Session-collapsed imports creates missing expected slots, preserves any existing version/media chain, and marks ambiguous legacy media for operator review rather than selecting a presenter automatically.

Central and Site expose matching rotation lifecycle terminology and contracts while retaining separate PostgreSQL ownership and Site offline autonomy. Site Manager continues to call Site only; no SMB or direct Agent path is introduced.
