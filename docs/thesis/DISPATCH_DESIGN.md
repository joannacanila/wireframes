# Dispatch: what exists and what a real dispatch feature would need (S16)

## What WIREFRAME does today

* A route is a list of poles an engineer ticks on the map. The order can be changed by hand or set to the
  **shortest straight-line order** (nearest neighbour, then 2-opt on straight-line distance). It is labelled as
  straight-line; road distance and travel time are not computed.
* "Open in Google Maps" hands the stops to Google Maps, which plans the road route. The route can be printed as a PDF
  and shared as a sign-in link.
* Poles with no usable location are listed under **Needs pinning** and cannot join a route until an administrator
  pins them (validated and audited).

There are no crews, vehicles, schedules, work orders or status updates from the field, and the interface does not
suggest there are.

## What a real dispatch feature would need

| Need | New data / service | Notes |
|---|---|---|
| Crews and availability | `crew`, `crew_member`, `shift` tables; who can be dispatched | Roles and permissions extend the existing staff accounts |
| Work orders | `work_order` (pole, inspection, priority, assigned crew, due date, state: open / dispatched / on site / done / cancelled), audit on every change | The inspection review stays separate: a review records a finding, a work order records action |
| Road routing | A routing service (e.g. OSRM self-hosted on OpenStreetMap data, or a commercial API) | Needs hosting or an API key and terms of use; results are estimates |
| Field updates | A mobile view for crews to mark arrival / completion, with photos | Offline support would be needed in poor coverage areas |
| Utility hand-off | Agreements with the electric utility and telecom providers on who receives what | WIREFRAME cannot dispatch another organisation's crews; it can only send a report |

Each row is a new data model or external service, so it needs the owner's decision before any implementation.
