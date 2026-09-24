# Contributing

Thanks for wanting to help. Bug reports, data corrections, new areas and code are all
welcome.

For how the code is organised and how to run it, see [development.md](development.md).
If you work with a coding agent, point it at [agent-guide.md](agent-guide.md).

## Ways to help

- **Report a bug.** Open an issue with the area, the preset and settings you used, what
  you expected and what happened. A screenshot of the map and the stats panel helps.
  For backend errors, include the traceback from the `traffic-sim-server` terminal.
- **Correct the data.** If you know Dhaka's roads, you can often spot what the model
  gets wrong: a missing waterlogging spot, a bus stop in the wrong place, a turn that is
  banned in real life, a vehicle mix that looks off for an area or time of day. Open an
  issue and include a source (news report, survey, photo) where you can. Road geometry
  itself comes from OpenStreetMap, so fixing it there fixes it here on the next
  `traffic-sim-prepare --download`.
- **Calibration data.** Real vehicle counts, travel times or signal timings for any
  Dhaka road are the most valuable thing you can contribute. The volumes and driver
  behaviour are still uncalibrated guesses (see *Reading the numbers* in
  [model.md](model.md)).
- **Add an area or a whole city.** Areas and cities are region packs: data files in
  `backend/src/traffic_sim/regions/`. See [regions.md](regions.md) for the format and
  [agent-guide.md](agent-guide.md) for the steps. Keep a neighbourhood under about
  3 × 3 km, and run `uv run traffic-sim-region check` before you open the PR.
- **Code.** Look for open issues, or open one describing what you want to change before
  starting anything large, so we can agree on the approach first.

## Making a change

1. Fork the repo and create a branch from `main`.
2. Make your change. Keep a pull request to one topic; unrelated fixes go in their own
   PR.
3. Run the checks:

   ```sh
   cd backend && uv run pytest
   cd frontend && npx tsc -b && npm run lint
   ```

   The backend tests run real SUMO simulations and are skipped unless the Farmgate
   area is built (`uv run traffic-sim-prepare farmgate`). Build it before you run them.
4. If you changed behaviour that users see, update the docs in `docs/` (or the README)
   and, where it applies, the in-app guide (`frontend/src/components/Docs.tsx`).
5. Open a pull request. Say what changed and why, and how you tested it. For UI changes,
   add a screenshot or a short recording.

## Guidelines

- **Match the surrounding code.** Same naming, same comment density, same idioms. The
  backend is plain Python with dataclasses and type hints; the frontend is React with
  TypeScript and no state library.
- **Explain Dhaka-specific numbers.** Any constant that stands for something in the real
  world (a speed, a share, a volume, a flood depth) needs a comment saying where it
  came from, as in `regions/dhaka/presets.toml` and `vtypes.py`. If it is a guess, say so.
- **Cite sources for data.** New entries in a region's `waterlogging.json` or similar
  data files need a source, like the existing ones.
- **Keep it fast.** Every open browser tab runs its own SUMO process, and the whole-city
  area has tens of thousands of vehicles. Watch per-step work in `engine.py` and the
  size of WebSocket frames.
- **Test backend changes.** Add or extend a test in `backend/tests/` for new validation
  rules, edits or protocol messages.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/), as the existing
history does:

```
feat(backend): timed road closures
fix(frontend): keep the selected road when the layout changes
docs: explain turn rules
```

Common types: `feat`, `fix`, `docs`, `test`, `refactor`, `build`, `chore`. Scope is
`backend` or `frontend` when the change is limited to one of them.

## License

By contributing, you agree that your contributions are licensed under the MIT License,
like the rest of the project (see [LICENSE](../LICENSE)).
